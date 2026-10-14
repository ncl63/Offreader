"""Orchestration : analyse du lecteur, téléchargement, vérification, export.

Le moteur ne fait aucun affichage : il émet des `ProgressEvent` via un rappel,
ce qui permet de brancher indifféremment la CLI ou une future interface graphique.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adapters import find_adapter
from .browser import BrowserSession
from .config import Config
from .detector import extract_with_mode
from .downloader import Downloader
from .errors import IncompleteChapter, NoImagesFound
from .exporter import ordered_files, safe_name, write_cbz
from .models import Extraction, ProgressEvent, Status
from .strategies.base import Deadline

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    extraction: Extraction
    output: Path  # dossier d'images ou fichier .cbz


async def extract(url: str, config: Config, on_event: Callable[[ProgressEvent], None]):
    """Analyse le lecteur. Renvoie l'extraction et l'identité HTTP du navigateur."""
    on_event(ProgressEvent("analysis", "Analyse du lecteur..."))
    deadline = Deadline(config.extraction_timeout)
    async with BrowserSession(config, on_event) as session:
        await session.open(url)
        adapter = find_adapter(url)
        if adapter:
            on_event(ProgressEvent("mode", f"adaptateur {adapter.name}"))
            extraction = await adapter(session, config, deadline, on_event).extract()
        else:
            extraction = await extract_with_mode(config.mode, session, config, deadline, on_event)
        cookies, headers = await session.http_identity()
    if not extraction.planches:
        raise NoImagesFound("Aucune planche n'a été détectée sur cette page.")
    for w in extraction.warnings:
        on_event(ProgressEvent("warning", w))
    return extraction, cookies, headers


async def run(url: str, config: Config, on_event: Callable[[ProgressEvent], None] | None = None) -> RunResult:
    on_event = on_event or (lambda e: None)
    extraction, cookies, headers = await extract(url, config, on_event)
    n = len(extraction.planches)
    on_event(ProgressEvent("pages", f"Planches détectées ({extraction.end_reason})", n, n))

    name = safe_name(config.name or extraction.title)
    out_dir = Path(config.output_dir)
    work = out_dir / name if config.format == "images" else out_dir / f"{name}.parts"

    downloader = Downloader(config, cookies, headers, on_event)
    await downloader.download_all(extraction.planches, work, source_url=url)

    failed = [p for p in extraction.planches if p.status == Status.FAILED]
    if failed:
        details = "\n".join(f"  - planche {p.index} : {p.error} ({p.url})" for p in failed[:10])
        raise IncompleteChapter(
            f"{len(failed)} planche(s) sur {n} n'ont pas pu être téléchargées :\n{details}\n"
            f"Les planches déjà récupérées sont conservées dans {work} ; "
            "relancez la même commande pour reprendre."
        )
    if extraction.expected_total and extraction.expected_total > n:
        raise IncompleteChapter(
            f"Le lecteur annonce {extraction.expected_total} pages mais seules {n} ont été trouvées "
            f"({extraction.end_reason}). Les planches récupérées sont dans {work}."
        )

    if config.format == "images":
        return RunResult(extraction, work)

    on_event(ProgressEvent("export", "Export CBZ..."))
    target = write_cbz(ordered_files(extraction.planches, work), out_dir / f"{name}.cbz")
    shutil.rmtree(work, ignore_errors=True)
    return RunResult(extraction, target)
