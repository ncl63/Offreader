"""Analyse des images présentes dans le DOM.

La collecte s'exécute dans la page (JavaScript) et renvoie, pour chaque <img>,
ses sources possibles, ses dimensions, sa position et sa structure DOM.
Le tri entre planches et images parasites est fait ensuite en Python
(fonctions pures, testables sans navigateur).
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urldefrag, urlsplit

# --------------------------------------------------------------------------- JS

COLLECT_JS = r"""
() => {
  const LAZY_ATTRS = ['data-src', 'data-lazy-src', 'data-original', 'data-url', 'data-lazy',
    'data-echo', 'data-full', 'data-full-src', 'data-image', 'data-img', 'data-cfsrc',
    'data-pagespeed-lazy-src', 'data-hi-res-src', 'data-zoom-src'];
  const LAZY_SRCSET = ['data-srcset', 'data-lazy-srcset'];
  const STATE_CLASS = /^(active|current|show|shown|visible|hidden|loaded|loading|lazy.*|is-.*|has-.*|selected|fade.*|in|ready|error)$/;

  const abs = (u) => { try { return new URL(u.trim(), document.baseURI).href; } catch (e) { return null; } };
  const isPlaceholder = (u) => {
    if (!u) return true;
    if (u.startsWith('data:')) return u.length < 2000;
    if (u.startsWith('about:') || u.startsWith('javascript:')) return true;
    const file = u.split(/[?#]/)[0].toLowerCase().split('/').pop();
    return /^(blank|placeholder|spacer|loading|loader|lazy|pixel|transparent|empty|grey|gray|spinner)([-_.][^/]*)?\.(gif|png|svg|jpe?g|webp)$/.test(file);
  };
  const bestFromSrcset = (s) => {
    if (!s) return null;
    let best = null, bestScore = -1;
    for (const part of s.split(/,\s+/)) {
      const bits = part.trim().split(/\s+/);
      if (!bits[0]) continue;
      let score = 1;
      const m = (bits[1] || '').match(/^([\d.]+)([wx])$/);
      if (m) score = parseFloat(m[1]) * (m[2] === 'x' ? 1000 : 1);
      if (score > bestScore) { bestScore = score; best = bits[0]; }
    }
    return best ? abs(best) : null;
  };
  const nodeKey = (el) => el.tagName.toLowerCase() + [...el.classList]
      .map(c => c.replace(/\d+/g, '#')).filter(c => c && !STATE_CLASS.test(c))
      .sort().map(c => '.' + c).join('');
  const tokens = (el) => [el.id || '', typeof el.className === 'string' ? el.className : '',
      el.getAttribute('role') || '', el.getAttribute('aria-label') || ''].join(' ');
  const isVisible = (el) => {
    if (el.checkVisibility) return el.checkVisibility({checkVisibilityCSS: true});
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0;
  };

  const scroller = window.__offreaderScroller || null;
  const extraTop = scroller ? scroller.scrollTop : 0;
  const imgs = [...document.querySelectorAll('img')];
  window.__offreaderNextId = window.__offreaderNextId || 1;
  const out = [];
  imgs.forEach((img, dom) => {
    if (!img.dataset.offreaderId) img.dataset.offreaderId = String(window.__offreaderNextId++);
    const raw = img.getAttribute('src') || '';
    let url = null;
    const srcsetBest = bestFromSrcset(img.getAttribute('srcset'));
    const current = img.currentSrc || (raw ? abs(raw) : null);
    if (srcsetBest && !isPlaceholder(srcsetBest)) url = srcsetBest;
    else if (current && !isPlaceholder(current)) url = current;
    if (!url) {
      for (const a of LAZY_SRCSET) { const v = bestFromSrcset(img.getAttribute(a)); if (v && !isPlaceholder(v)) { url = v; break; } }
    }
    if (!url) {
      for (const a of LAZY_ATTRS) { const v = img.getAttribute(a); if (v && !isPlaceholder(abs(v))) { url = abs(v); break; } }
    }
    // Nom évoquant un espace réservé (« blank.jpg ») mais image réellement grande : c'est une planche.
    if (!url && current && !current.startsWith('data:') && img.naturalWidth >= 200 && img.naturalHeight >= 200) url = current;
    const path = [];
    const anc = [];
    let p = img.parentElement, depth = 0;
    while (p && p !== document.body && p !== document.documentElement) {
      path.push(nodeKey(p));
      if (depth < 4) anc.push(tokens(p));
      p = p.parentElement; depth++;
    }
    const r = img.getBoundingClientRect();
    out.push({
      id: img.dataset.offreaderId,
      dom,
      url,
      natural_w: img.naturalWidth, natural_h: img.naturalHeight,
      render_w: Math.round(r.width), render_h: Math.round(r.height),
      attr_w: parseInt(img.getAttribute('width')) || 0, attr_h: parseInt(img.getAttribute('height')) || 0,
      top: Math.round(r.top + window.scrollY + extraTop), left: Math.round(r.left + window.scrollX),
      complete: img.complete && img.naturalWidth > 0,
      broken: img.complete && img.naturalWidth === 0 && !!url,
      hidden: !isVisible(img),
      in_viewport: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth && r.width > 0,
      path: path.reverse().join('>'),
      own: [tokens(img), img.alt || '', img.title || ''].join(' '),
      ancestors: anc.join(' '),
    });
  });
  return {doc_width: Math.max(document.documentElement.scrollWidth, innerWidth), images: out};
}
"""

SCROLL_STEP_JS = r"""
(ratio) => {
  const root = document.scrollingElement || document.documentElement;
  if (window.__offreaderScroller === undefined) {
    // Défilement de la fenêtre, ou d'un conteneur interne si la page elle-même ne défile pas.
    let best = null, bestRange = 0;
    if (root.scrollHeight - innerHeight < 50) {
      for (const e of document.querySelectorAll('body *')) {
        const range = e.scrollHeight - e.clientHeight;
        if (range > 50 && range > bestRange && /(auto|scroll)/.test(getComputedStyle(e).overflowY)) {
          best = e; bestRange = range;
        }
      }
    }
    window.__offreaderScroller = best;
  }
  const el = window.__offreaderScroller || root;
  const view = el === root ? innerHeight : el.clientHeight;
  const before = el.scrollTop;
  el.scrollTop = before + view * ratio;
  const after = el.scrollTop;
  return {moved: after > before + 1, at_bottom: after >= el.scrollHeight - view - 2, height: el.scrollHeight};
}
"""

SCROLL_TOP_JS = r"""
() => { const el = window.__offreaderScroller || document.scrollingElement || document.documentElement; el.scrollTop = 0; }
"""

SCROLL_INTO_VIEW_JS = r"""
(id) => { const el = document.querySelector(`img[data-offreader-id="${id}"]`); if (el) el.scrollIntoView({block: 'center'}); return !!el; }
"""

# ---------------------------------------------------------------- Python side

MIN_SIDE = 200  # largeur ou hauteur minimale d'une planche (px)
MIN_AREA = 60_000

# Mots-clés : jetons exacts (découpage sur tout caractère non alphanumérique).
NEGATIVE_OWN = {
    "logo", "ad", "ads", "advert", "adverts", "advertisement", "adsbygoogle", "banner",
    "sponsor", "sponsored", "avatar", "icon", "icons", "emoji", "badge", "pub", "publicite",
    "thumb", "thumbs", "thumbnail", "thumbnails", "favicon",
}
NEGATIVE_CONTEXT = NEGATIVE_OWN | {
    "social", "share", "footer", "header", "nav", "navbar", "menu", "sidebar", "aside",
    "comment", "comments", "related", "recommend", "recommended", "widget", "widgets", "promo",
    "popular", "trending", "suggestions",
}
POSITIVE_CONTEXT = {
    "reader", "reading", "chapter", "chap", "manga", "comic", "comics", "viewer", "scan",
    "lecteur", "webtoon", "page", "pages", "planche", "planches", "strip",
}
AD_HOSTS = ("doubleclick", "googlesyndication", "adservice", "adnxs", "taboola", "outbrain")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


@dataclass
class ImageInfo:
    id: str
    dom: int
    url: str | None
    natural_w: int
    natural_h: int
    render_w: int
    render_h: int
    attr_w: int
    attr_h: int
    top: int
    left: int
    complete: bool
    broken: bool
    hidden: bool
    in_viewport: bool
    path: str
    own: str
    ancestors: str

    @classmethod
    def from_js(cls, d: dict) -> "ImageInfo":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})

    @property
    def width(self) -> int:
        return self.natural_w or self.attr_w or self.render_w

    @property
    def height(self) -> int:
        return self.natural_h or self.attr_h or self.render_h

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def pending(self) -> bool:
        """Image pas (encore) chargée : dimensions inconnues."""
        return not self.complete

    def is_large(self) -> bool:
        return self.width >= MIN_SIDE and self.height >= MIN_SIDE and self.area >= MIN_AREA

    def own_negative(self) -> bool:
        toks = tokenize(self.own)
        if self.url and not self.url.startswith("data:"):
            parts = urlsplit(self.url)
            if any(h in parts.netloc.lower() for h in AD_HOSTS):
                return True
            toks |= tokenize(parts.path)
        return bool(toks & NEGATIVE_OWN)


def normalize_url(url: str) -> str:
    """Clé de déduplication : l'URL sans fragment (la requête est conservée)."""
    return urldefrag(url)[0] if not url.startswith("data:") else url


def parse_images(payload: dict) -> tuple[list[ImageInfo], int]:
    return [ImageInfo.from_js(d) for d in payload["images"]], int(payload.get("doc_width") or 0)


def _group_score(group: list[ImageInfo], doc_width: int) -> float:
    score = float(sum(i.area for i in group))
    context = tokenize(group[0].path + " " + " ".join(i.ancestors for i in group[:3]))
    if context & NEGATIVE_CONTEXT:
        score *= 0.3
    if context & POSITIVE_CONTEXT:
        score *= 1.5
    if doc_width:
        center = statistics.mean(i.left + i.width / 2 for i in group)
        if abs(center - doc_width / 2) > doc_width * 0.25:
            score *= 0.5  # le contenu principal est en général centré
    return score


def _parent_path(path: str) -> str:
    return path.rsplit(">", 1)[0] if ">" in path else ""


def select_pages(images: list[ImageInfo], doc_width: int = 0) -> list[ImageInfo]:
    """Sélectionne les planches du contenu principal, dans l'ordre de lecture.

    1. On écarte les images invisibles, sans source réelle ou marquées comme
       logo, publicité, miniature, etc.
    2. Les grandes images chargées sont regroupées par structure DOM (chemin
       des conteneurs) ; le groupe le plus important (surface, position
       centrale, indices sémantiques) est le contenu principal.
    3. Les images de même structure pas encore chargées (lazy loading, erreur)
       sont conservées : ce sont des planches dont il faudra vérifier le
       téléchargement, pas des parasites.
    4. Ordre : position verticale dans le document, puis ordre DOM.
    5. Doublons : même URL normalisée.
    """
    usable = [i for i in images if i.url and not i.hidden and not i.own_negative()]
    loaded = [i for i in usable if i.complete and i.is_large()]
    if not loaded:
        # Rien de chargé : on se rabat sur la structure seule (images en attente).
        pending = [i for i in usable if i.pending and i.width >= MIN_SIDE]
        if not pending:
            return []
        loaded_groups: dict[str, list[ImageInfo]] = defaultdict(list)
        for i in pending:
            loaded_groups[i.path].append(i)
        main_path = max(loaded_groups, key=lambda p: len(loaded_groups[p]))
        return _order_and_dedupe(loaded_groups[main_path])

    groups: dict[str, list[ImageInfo]] = defaultdict(list)
    for i in loaded:
        groups[i.path].append(i)
    main_path = max(groups, key=lambda p: _group_score(groups[p], doc_width))
    main = groups[main_path]
    med_w = statistics.median(i.width for i in main)
    med_ratio = statistics.median(i.ratio for i in main)

    # Variantes de structure dans le même conteneur (ex. div.page / div.page.double).
    accepted_paths = {main_path}
    parent = _parent_path(main_path)
    for path, group in groups.items():
        if path in accepted_paths or not parent or _parent_path(path) != parent:
            continue
        last = path.rsplit(">", 1)[-1]
        if tokenize(last) & NEGATIVE_CONTEXT:
            continue
        g_w = statistics.median(i.width for i in group)
        if abs(g_w - med_w) <= 0.15 * med_w:
            accepted_paths.add(path)

    pages = []
    for i in usable:
        if i.path not in accepted_paths:
            continue
        if i.complete:
            if not i.is_large() or i.width < 0.5 * med_w:
                continue
            # Bannière glissée dans le lecteur : largeur différente ET format très allongé.
            if abs(i.width - med_w) > 0.05 * med_w and med_ratio and i.ratio > 3 * med_ratio:
                continue
        pages.append(i)
    return _order_and_dedupe(pages)


def _order_and_dedupe(pages: list[ImageInfo]) -> list[ImageInfo]:
    pages = sorted(pages, key=lambda i: (round(i.top / 10), i.dom))
    seen: set[str] = set()
    result = []
    for i in pages:
        key = normalize_url(i.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(i)
    return result


def main_image(images: list[ImageInfo], prefer_path: str | None = None) -> ImageInfo | None:
    """Image principale d'un lecteur page par page : la plus grande image visible."""
    usable = [
        i for i in images
        if i.url and not i.hidden and i.in_viewport and not i.own_negative()
        and (i.is_large() or i.pending)
    ]
    if prefer_path is not None:
        same = [i for i in usable if i.path == prefer_path]
        if same:
            usable = same
    loaded = [i for i in usable if i.complete and i.is_large()]
    pool = loaded or [i for i in usable if i.pending]
    if not pool:
        return None
    return max(pool, key=lambda i: (i.render_w * i.render_h, i.area))
