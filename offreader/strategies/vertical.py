"""Stratégie A : lecteur à défilement vertical (toutes les planches dans une page)."""

from __future__ import annotations

import asyncio
import logging
import time

from playwright.async_api import Error as PlaywrightError

from ..dom import SCROLL_INTO_VIEW_JS, SCROLL_STEP_JS, SCROLL_TOP_JS, ImageInfo, select_pages
from ..models import Extraction, Mode, Planche, ProgressEvent
from .base import Strategy

log = logging.getLogger(__name__)


class VerticalStrategy(Strategy):
    mode = Mode.VERTICAL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Observations cumulées : id d'élément -> liste d'observations (une par URL distincte).
        self._seen: dict[str, list[ImageInfo]] = {}
        self._doc_width = 0

    async def scan(self) -> list[ImageInfo]:
        """Fait défiler toute la page puis renvoie les planches détectées (sans les exporter)."""
        await self.page.evaluate(SCROLL_TOP_JS)
        await self._snapshot()
        stable_since, last_height = None, -1
        for step in range(self.config.max_scroll_steps):
            if self.deadline.expired:
                log.warning("Délai d'analyse dépassé pendant le défilement")
                break
            state = await self.page.evaluate(SCROLL_STEP_JS, 0.85)
            await self._pause()
            await self._snapshot()
            if state["at_bottom"] and state["height"] == last_height:
                # En bas : la hauteur doit rester stable un moment (contenu ajouté à la volée).
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= self.config.bottom_settle:
                    log.debug("Bas de page atteint après %d pas", step + 1)
                    break
            else:
                stable_since = None
            last_height = state["height"]
        else:
            log.warning("Nombre maximal de pas de défilement atteint (%d)", self.config.max_scroll_steps)

        pages = select_pages(self._merged(), self._doc_width)
        await self._wait_pending(pages)
        return select_pages(self._merged(), self._doc_width)

    async def extract(self) -> Extraction:
        pages = await self.scan()
        planches = [Planche(index=n, url=i.url, page_label=str(n)) for n, i in enumerate(pages, 1)]
        await self.materialize(planches)
        return Extraction(planches=planches, mode=self.mode, title=await self.title(),
                          page_url=self.page.url, end_reason="bas de la page atteint")

    async def _pause(self) -> None:
        await asyncio.sleep(self.config.scroll_delay)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=1000)
        except PlaywrightError:
            pass

    async def _snapshot(self) -> None:
        images, self._doc_width = await self.collect()
        for img in images:
            if not img.url:
                continue
            history = self._seen.setdefault(img.id, [])
            if history and (history[-1].url == img.url or abs(history[-1].top - img.top) < 50):
                history[-1] = img  # même planche (chargée, ou version haute résolution)
            else:
                history.append(img)  # élément recyclé pour une autre planche

    def _merged(self) -> list[ImageInfo]:
        return [img for history in self._seen.values() for img in history]

    async def _wait_pending(self, pages: list[ImageInfo]) -> None:
        """Amène à l'écran les planches non chargées et attend leur chargement (borné)."""
        pending = [i for i in pages if i.pending and not i.broken]
        if not pending:
            return
        self.on_event(ProgressEvent("analysis", f"Chargement de {len(pending)} planche(s) différée(s)..."))
        end = time.monotonic() + min(self.config.step_timeout, self.deadline.remaining)
        for img in pending:
            await self.page.evaluate(SCROLL_INTO_VIEW_JS, img.id)
            await asyncio.sleep(self.config.scroll_delay)
        while time.monotonic() < end:
            await self._snapshot()
            ids = {i.id for i in pending}
            still = [i for i in self._merged() if i.id in ids and i.pending and not i.broken]
            if not still:
                return
            await asyncio.sleep(0.25)
        log.warning("%d planche(s) toujours non chargée(s) dans le navigateur", len(pending))
