"""Interface commune des stratégies et outils partagés."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable

from ..browser import BrowserSession
from ..config import Config
from ..dom import COLLECT_JS, ImageInfo, parse_images
from ..models import Extraction, Mode, Planche, ProgressEvent

EventCallback = Callable[[ProgressEvent], None]


class Deadline:
    """Échéance globale : garantit qu'aucune analyse ne dure indéfiniment."""

    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    @property
    def remaining(self) -> float:
        return max(0.0, self.end - time.monotonic())

    @property
    def expired(self) -> bool:
        return self.remaining <= 0


class Strategy(ABC):
    mode: Mode

    def __init__(self, session: BrowserSession, config: Config, deadline: Deadline,
                 on_event: EventCallback | None = None):
        self.session = session
        self.config = config
        self.deadline = deadline
        self.on_event = on_event or (lambda e: None)

    @property
    def page(self):
        return self.session.page

    async def collect(self) -> tuple[list[ImageInfo], int]:
        return parse_images(await self.page.evaluate(COLLECT_JS))

    async def materialize(self, planches: list[Planche]) -> None:
        """Les URL blob: n'existent que dans la page : on lit leur contenu tout de suite."""
        for p in planches:
            if p.url.startswith("blob:") and p.data is None:
                p.data = await self.session.fetch_blob(p.url)

    async def title(self) -> str:
        try:
            return (await self.page.title()).strip()
        except Exception:  # page en cours de navigation
            return ""

    @abstractmethod
    async def extract(self) -> Extraction:
        ...
