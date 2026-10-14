"""Stratégie B : lecteur page par page (une seule planche affichée à la fois)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from ..dom import ImageInfo, main_image, normalize_url
from ..errors import NoImagesFound
from ..models import Extraction, Mode, Planche, ProgressEvent
from .base import Strategy

log = logging.getLogger(__name__)

# Repère le bouton « page suivante » (et écarte « chapitre suivant »).
FIND_NEXT_JS = r"""
() => {
  document.querySelectorAll('[data-offreader-next]').forEach(e => e.removeAttribute('data-offreader-next'));
  const NEXT = /(^|[^a-z])(next|suivant|suivante|siguiente|nächste|prossim|avanti|forward)([^a-z]|$)/i;
  const ARROWS = /^\s*(>|›|»|→|▶|►|❯|⟩|>>)\s*$/;
  const CHAPTER = /(chap|episode|épisode|\bch\.|issue|volume|tome)/i;
  const PREV = /(prev|précédent|precedent|back|retour)/i;
  const sel = 'a, button, [role=button], [onclick], input[type=button], input[type=submit], [class*=next i], [id*=next i], [aria-label]';
  let best = null, bestScore = 0;
  for (const el of document.querySelectorAll(sel)) {
    const text = (el.innerText || el.value || '').trim();
    const meta = [el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('rel'),
                  el.id, typeof el.className === 'string' ? el.className : ''].join(' ');
    const all = text + ' ' + meta;
    if (CHAPTER.test(all) || PREV.test(all)) continue;
    let score = 0;
    if (ARROWS.test(text)) score += 2;
    if (NEXT.test(text)) score += 3;
    if (NEXT.test(meta)) score += 2;
    if ((el.getAttribute('rel') || '').toLowerCase() === 'next') score += 2;
    if (!score) continue;
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (s.display === 'none' || s.visibility === 'hidden' || r.width === 0 || r.height === 0) continue;
    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' ||
                     /(^|\s)disabled(\s|$)/.test(el.className || '') || s.pointerEvents === 'none';
    if (disabled) continue;
    if (score > bestScore) { best = el; bestScore = score; }
  }
  if (best) best.setAttribute('data-offreader-next', '1');
  return !!best;
}
"""

# Compteur de pages : « 3 / 20 », « page 3 of 20 », ou liste déroulante de pages.
COUNTER_JS = r"""
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  for (const s of document.querySelectorAll('select')) {
    if (s.options.length < 2 || !visible(s)) continue;
    const labels = [...s.options].map(o => o.text.trim());
    if (labels.some(l => /(chap|episode|épisode|volume|tome)/i.test(l))) continue;
    if (labels.every(l => /^(page|p\.?|pg)?\s*\d+(\s*(\/|of|sur)\s*\d+)?$/i.test(l))) {
      s.setAttribute('data-offreader-select', '1');
      return {current: s.selectedIndex + 1, total: s.options.length, source: 'select'};
    }
  }
  const re = /(?:^|[^\d\/])(\d{1,4})\s*(?:\/|of|sur|de)\s*(\d{1,4})(?![\d\/])/i;
  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length > 2 || ['SCRIPT', 'STYLE', 'TITLE'].includes(el.tagName)) continue;
    const t = (el.innerText || '').trim();
    if (!t || t.length > 40 || !visible(el)) continue;
    const m = t.match(re);
    if (m) {
      const c = +m[1], total = +m[2];
      if (c >= 1 && c <= total && total >= 2) return {current: c, total, source: 'text'};
    }
  }
  return null;
}
"""

SELECT_NEXT_JS = r"""
() => {
  const s = document.querySelector('select[data-offreader-select]');
  if (!s || s.selectedIndex >= s.options.length - 1) return false;
  s.selectedIndex += 1;
  s.dispatchEvent(new Event('input', {bubbles: true}));
  s.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}
"""

_PAGE_URL_PATTERNS = [
    re.compile(r"([?&](?:page|p|pg|pagina|seite)=)(\d+)", re.I),
    re.compile(r"(#(?:page=|p=|page-|p)?)(\d+)$", re.I),
    re.compile(r"(/(?:page|p|pages|pg)[-_/]?)(\d+)(?=/?(?:[?#]|$))", re.I),
]


def next_page_url(url: str, current: int) -> str | None:
    """Incrémente un numéro de page explicite dans l'URL (jamais un numéro de chapitre)."""
    for pattern in _PAGE_URL_PATTERNS:
        matches = list(pattern.finditer(url))
        if matches and int(matches[-1].group(2)) == current:
            m = matches[-1]
            return url[: m.start(2)] + str(current + 1) + url[m.end(2):]
    return None


def url_change_kind(old: str, new: str) -> str:
    """'same' (URL inchangée), 'page' (seul un numéro de page change) ou 'other'."""
    if normalize_url(old) == normalize_url(new) and urlsplit(old).fragment == urlsplit(new).fragment:
        return "same"
    a, b = urlsplit(old), urlsplit(new)
    if a.netloc != b.netloc:
        return "other"
    if a.path == b.path:
        return "page"  # seuls la requête ou le fragment changent
    na, nb = re.findall(r"\d+", a.path), re.findall(r"\d+", b.path)
    if re.sub(r"\d+", "#", a.path) == re.sub(r"\d+", "#", b.path) and len(na) == len(nb) and na:
        if na[:-1] == nb[:-1]:
            return "page"
    return "other"


class Mechanism:
    """Un moyen de passer à la page suivante."""

    name = "?"

    async def trigger(self, strat: "PagedStrategy") -> bool:
        raise NotImplementedError


class NextButton(Mechanism):
    name = "bouton « suivant »"

    async def trigger(self, strat):
        if not await strat.page.evaluate(FIND_NEXT_JS):
            return False
        loc = strat.page.locator("[data-offreader-next]").first
        try:
            await loc.click(timeout=3000)
        except PlaywrightError:
            try:  # élément recouvert : clic programmatique
                await loc.evaluate("el => el.click()", timeout=3000)
            except PlaywrightError:
                return False
        return True


class Key(Mechanism):
    def __init__(self, key: str):
        self.key = key
        self.name = f"touche {key}"

    async def trigger(self, strat):
        await strat.page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        await strat.page.keyboard.press(self.key)
        return True


class ClickImage(Mechanism):
    name = "clic sur l'image"

    async def trigger(self, strat):
        if not strat.current:
            return False
        loc = strat.page.locator(f'img[data-offreader-id="{strat.current.id}"]')
        try:
            await loc.click(timeout=3000)
        except PlaywrightError:
            return False
        return True


class UrlIncrement(Mechanism):
    name = "numéro de page dans l'URL"

    async def trigger(self, strat):
        target = next_page_url(strat.page.url, strat.current_number)
        if not target:
            return False
        try:
            response = await strat.page.goto(target, wait_until="domcontentloaded")
        except PlaywrightError:
            return False
        return response is None or response.status < 400


class SelectOption(Mechanism):
    name = "liste des pages"

    async def trigger(self, strat):
        return await strat.page.evaluate(SELECT_NEXT_JS)


class PagedStrategy(Strategy):
    mode = Mode.PAGED

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mechanisms: list[Mechanism] = [
            NextButton(), Key("ArrowRight"), ClickImage(), UrlIncrement(), SelectOption(), Key("ArrowLeft"),
        ]
        self.current: ImageInfo | None = None
        self.planches: list[Planche] = []
        self._seen: set[str] = set()
        self._path: str | None = None
        self._counter: dict | None = None
        self._counter_ok = False  # le compteur suit-il réellement la navigation ?
        self._url_changes: bool | None = None  # la navigation modifie-t-elle l'URL ?

    @property
    def current_number(self) -> int:
        if self._counter and self._counter_ok:
            return self._counter["current"]
        return len(self.planches)

    @property
    def total(self) -> int | None:
        return self._counter["total"] if self._counter and self._counter_ok else None

    async def extract(self) -> Extraction:
        first = await self._wait_main(previous=None, timeout=self.config.step_timeout)
        if first is None:
            raise NoImagesFound("Aucune planche n'a été trouvée sur cette page. Vérifiez qu'il s'agit bien de la page de lecture du chapitre.")
        self._path = first.path
        await self._record(first)

        established: Mechanism | None = None
        end_reason = ""
        while True:
            total = self.total
            if total and self._counter["current"] >= total:
                end_reason = f"dernière page atteinte ({total})"
                break
            if len(self.planches) >= self.config.max_pages:
                end_reason = f"nombre maximal de pages atteint ({self.config.max_pages})"
                break
            if self.deadline.expired:
                end_reason = "délai maximal d'analyse dépassé"
                break
            candidates = [established] if established else self.mechanisms
            result, reason = None, "aucun mécanisme de navigation ne fait avancer le lecteur"
            for mech in candidates:
                result, reason = await self._try(mech, patient=established is not None)
                if result is not None:
                    if established is None:
                        log.info("Navigation détectée : %s", mech.name)
                    established = mech
                    break
                if reason.startswith("stop:"):
                    break
            if result is None:
                end_reason = reason.removeprefix("stop:")
                break
            await self._record(result)

        if not end_reason:
            end_reason = "fin du chapitre"
        extraction = Extraction(planches=self.planches, mode=self.mode, title=await self.title(),
                                page_url=self.page.url, end_reason=end_reason)
        extraction.expected_total = self.total
        if "maximal" in end_reason:
            extraction.warnings.append(f"Arrêt avant la fin éventuelle du chapitre : {end_reason}.")
        return extraction

    async def _try(self, mech: Mechanism, patient: bool) -> tuple[ImageInfo | None, str]:
        before_url = self.page.url
        prev = self.current
        try:
            triggered = await mech.trigger(self)
        except PlaywrightError as exc:
            log.debug("%s : %s", mech.name, exc)
            triggered = False
        if not triggered:
            return None, "fin du chapitre (plus de page suivante)"
        timeout = self.config.step_timeout if patient else self.config.change_timeout
        new = await self._wait_main(previous=prev, timeout=timeout)
        if new is None:
            if self.page.url != before_url and url_change_kind(before_url, self.page.url) != "same":
                return None, "stop:fin du chapitre (la page suivante ne contient pas de planche)"
            return None, "fin du chapitre (le lecteur n'avance plus)"

        kind = url_change_kind(before_url, self.page.url)
        if kind == "other" or (kind == "page" and self._url_changes is False):
            return None, "stop:fin du chapitre (le lecteur a quitté le chapitre)"
        if normalize_url(new.url) in self._seen:
            return None, "stop:fin du chapitre (retour à une page déjà vue)"
        if self._url_changes is None:
            self._url_changes = kind != "same"
        return new, ""

    async def _record(self, img: ImageInfo) -> None:
        self.current = img
        self._seen.add(normalize_url(img.url))
        previous = self._counter
        self._counter = await self._read_counter()
        if previous and self._counter and self._counter["total"] == previous["total"]:
            # Validé dès qu'une navigation fait avancer le compteur d'exactement une page.
            if self._counter["current"] == previous["current"] + 1:
                self._counter_ok = True
            elif self._counter_ok:
                self._counter_ok = False
                self.on_event(ProgressEvent("warning", "Le compteur de pages du lecteur est incohérent, il est ignoré."))
        elif previous or not self._counter:
            self._counter_ok = False
        label = str(self._counter["current"]) if self._counter_ok else str(len(self.planches) + 1)
        planche = Planche(index=len(self.planches) + 1, url=img.url, page_label=label)
        await self.materialize([planche])
        self.planches.append(planche)
        total = self.total or 0
        self.on_event(ProgressEvent("pages", "Pages récupérées", len(self.planches), total))

    async def _read_counter(self) -> dict | None:
        try:
            counter = await self.page.evaluate(COUNTER_JS)
        except PlaywrightError:
            return None
        if counter and counter["total"] > self.config.max_pages * 4:
            return None
        return counter

    async def _wait_main(self, previous: ImageInfo | None, timeout: float) -> ImageInfo | None:
        """Attend une nouvelle image principale effectivement chargée (borné par `timeout`)."""
        end = time.monotonic() + min(timeout, max(self.deadline.remaining, 0.5))
        changed: ImageInfo | None = None
        while time.monotonic() < end:
            try:
                images, _ = await self.collect()
                img = main_image(images, self._path)
            except PlaywrightError:
                img = None  # navigation en cours
            if img and (previous is None or normalize_url(img.url) != normalize_url(previous.url)):
                changed = img
                if img.complete or img.broken:
                    return img
            await asyncio.sleep(0.15)
        if changed is not None:
            log.warning("Planche %s non chargée dans le délai imparti", changed.url)
            self.on_event(ProgressEvent("warning", f"Planche lente à charger : {changed.url}"))
        return changed
