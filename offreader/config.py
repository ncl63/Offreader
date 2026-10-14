"""Paramètres d'exécution. Toutes les attentes sont bornées."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def default_output_dir() -> Path:
    return Path.home() / "Downloads"


@dataclass
class Config:
    output_dir: Path = field(default_factory=default_output_dir)
    format: str = "cbz"  # "cbz" | "images"
    mode: str = "auto"  # "auto" | "vertical" | "paged"
    name: str | None = None  # nom du chapitre ; par défaut le titre de la page
    headless: bool = True
    browser_executable: str | None = field(
        default_factory=lambda: os.environ.get("OFFREADER_CHROMIUM") or None
    )
    auto_install_browser: bool = True  # télécharge Chromium si aucun navigateur n'est trouvé

    # Navigateur
    navigation_timeout: float = 30.0  # chargement d'une page
    step_timeout: float = 15.0  # chargement d'une nouvelle planche
    change_timeout: float = 4.0  # essai d'un mécanisme de navigation inconnu
    scroll_delay: float = 0.35  # pause entre deux pas de défilement
    bottom_settle: float = 1.5  # durée de stabilité exigée en bas de page
    max_scroll_steps: int = 400
    extraction_timeout: float = 600.0  # durée maximale totale de l'analyse
    max_pages: int = 500

    # Téléchargement
    download_timeout: float = 30.0
    max_retries: int = 4
    retry_base_delay: float = 0.5
    workers: int = 4

    viewport: tuple[int, int] = (1280, 900)
