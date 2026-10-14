"""Détection du type de lecteur et choix de la stratégie."""

from __future__ import annotations

import logging

from .dom import SCROLL_TOP_JS
from .models import Extraction, Mode, Planche, ProgressEvent
from .strategies import PagedStrategy, VerticalStrategy
from .strategies.base import Deadline, EventCallback

log = logging.getLogger(__name__)


async def detect_and_extract(session, config, deadline: Deadline, on_event: EventCallback) -> Extraction:
    """Mode automatique.

    Le défilement complet est de toute façon nécessaire pour un lecteur vertical
    (lazy loading) : on le fait d'abord. S'il révèle au moins deux planches de
    même structure, c'est un lecteur vertical ; sinon on passe en page par page.
    """
    vertical = VerticalStrategy(session, config, deadline, on_event)
    pages = await vertical.scan()
    log.debug("Défilement : %d planche(s) candidate(s)", len(pages))
    if len(pages) >= 2:
        on_event(ProgressEvent("mode", Mode.VERTICAL.label))
        planches = [Planche(index=n, url=i.url, page_label=str(n)) for n, i in enumerate(pages, 1)]
        await vertical.materialize(planches)
        return Extraction(planches=planches, mode=Mode.VERTICAL, title=await vertical.title(),
                          page_url=session.page.url, end_reason="bas de la page atteint")

    on_event(ProgressEvent("mode", Mode.PAGED.label))
    await session.page.evaluate(SCROLL_TOP_JS)
    return await PagedStrategy(session, config, deadline, on_event).extract()


async def extract_with_mode(mode: str, session, config, deadline, on_event) -> Extraction:
    if mode == "vertical":
        on_event(ProgressEvent("mode", Mode.VERTICAL.label + " (imposé)"))
        return await VerticalStrategy(session, config, deadline, on_event).extract()
    if mode == "paged":
        on_event(ProgressEvent("mode", Mode.PAGED.label + " (imposé)"))
        return await PagedStrategy(session, config, deadline, on_event).extract()
    return await detect_and_extract(session, config, deadline, on_event)
