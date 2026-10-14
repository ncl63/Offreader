"""Représentation commune des planches, partagée par toutes les stratégies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PENDING = "en attente"
    DOWNLOADED = "téléchargée"
    SKIPPED = "déjà présente"  # reprise : fichier valide déjà sur le disque
    FAILED = "échec"


class Mode(str, Enum):
    VERTICAL = "vertical"
    PAGED = "paged"

    @property
    def label(self) -> str:
        return "défilement vertical" if self is Mode.VERTICAL else "page par page"


@dataclass
class Planche:
    index: int  # position dans l'ordre de lecture, à partir de 1
    url: str
    status: Status = Status.PENDING
    page_label: str | None = None  # numéro affiché par le lecteur, si détecté
    filename: str | None = None
    error: str | None = None
    size: int | None = None
    data: bytes | None = field(default=None, repr=False)  # contenu déjà récupéré (blob:)

    @property
    def done(self) -> bool:
        return self.status in (Status.DOWNLOADED, Status.SKIPPED)


@dataclass
class Extraction:
    """Résultat d'une stratégie d'extraction."""

    planches: list[Planche]
    mode: Mode
    title: str = ""
    page_url: str = ""
    expected_total: int | None = None  # total annoncé par le lecteur (« 3 / 20 »)
    end_reason: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProgressEvent:
    """Événement de progression transmis à l'interface (CLI aujourd'hui, GUI demain)."""

    kind: str  # "analysis" | "mode" | "pages" | "download" | "export" | "done" | "warning"
    message: str = ""
    current: int = 0
    total: int = 0
