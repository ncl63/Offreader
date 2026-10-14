"""Interface en ligne de commande."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Config, default_output_dir
from .engine import run
from .errors import IncompleteChapter, OffreaderError
from .models import ProgressEvent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manga_downloader",
        description="Extrait les planches d'un lecteur web, dans l'ordre, vers un dossier ou un CBZ.",
    )
    p.add_argument("url", help="URL du chapitre à télécharger")
    p.add_argument("--format", choices=["cbz", "images"], default="cbz", help="format de sortie (défaut : cbz)")
    p.add_argument("--output", type=Path, default=None,
                   help=f"dossier de destination (défaut : {default_output_dir()})")
    p.add_argument("--name", help="nom du chapitre (défaut : titre de la page)")
    p.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True,
                   help="masquer Chromium (défaut) ; --no-headless pour l'afficher")
    p.add_argument("--mode", choices=["auto", "vertical", "paged"], default="auto",
                   help="type de lecteur (défaut : détection automatique)")
    p.add_argument("--max-pages", type=int, default=500, help="nombre maximal de pages (défaut : 500)")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="délai maximal en secondes pour un chargement ou un téléchargement (défaut : 30)")
    p.add_argument("--retries", type=int, default=4, help="tentatives supplémentaires par image (défaut : 4)")
    p.add_argument("--workers", type=int, default=4, help="téléchargements simultanés (défaut : 4)")
    p.add_argument("--verbose", "-v", action="store_true", help="journalisation détaillée")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def config_from_args(args: argparse.Namespace) -> Config:
    cfg = Config(format=args.format, mode=args.mode, name=args.name, headless=args.headless,
                 max_pages=args.max_pages, max_retries=args.retries, workers=args.workers,
                 navigation_timeout=args.timeout, download_timeout=args.timeout,
                 step_timeout=min(args.timeout, 15.0))
    if args.output:
        cfg.output_dir = args.output
    return cfg


class ConsoleProgress:
    """Affichage de la progression ; une ligne par étape, mise à jour sur place."""

    def __init__(self, stream=sys.stdout):
        self.stream = stream
        self.inline = False
        self.interactive = stream.isatty()

    def __call__(self, e: ProgressEvent) -> None:
        if e.kind == "mode":
            self.line(f"Mode détecté : {e.message}")
        elif e.kind == "pages":
            total = f" / {e.total}" if e.total else ""
            self.update(f"{e.message} : {e.current}{total}", final=e.current == e.total)
        elif e.kind == "download":
            pct = int(100 * e.current / e.total) if e.total else 100
            self.update(f"Téléchargement : {pct} % ({e.current}/{e.total})", final=e.current == e.total)
        elif e.kind == "warning":
            self.line(f"Attention : {e.message}")
        else:
            self.line(e.message)

    def update(self, text: str, final: bool = False) -> None:
        if self.interactive:
            self.stream.write("\r" + text.ljust(60))
            self.inline = not final
            if final:
                self.stream.write("\n")
        elif final:
            self.stream.write(text + "\n")
        self.stream.flush()

    def line(self, text: str) -> None:
        if self.inline:
            self.stream.write("\n")
            self.inline = False
        self.stream.write(text + "\n")
        self.stream.flush()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # console Windows : jamais d'erreur d'encodage
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    progress = ConsoleProgress()
    try:
        result = asyncio.run(run(args.url, config_from_args(args), progress))
    except IncompleteChapter as exc:
        progress.line(f"Incomplet : {exc}")
        return 2
    except OffreaderError as exc:
        progress.line(f"Erreur : {exc}")
        return 1
    except KeyboardInterrupt:
        progress.line("Interrompu. Relancez la même commande pour reprendre.")
        return 130
    progress.line(f"Terminé : {len(result.extraction.planches)} planches -> {result.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
