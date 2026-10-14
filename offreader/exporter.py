"""Export des planches : dossier d'images ou archive CBZ."""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

from .errors import OffreaderError
from .models import Planche

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str, default: str = "chapitre") -> str:
    """Nom de fichier valide sous Windows."""
    name = re.sub(r"\s*:\s*", " - ", name or "")  # « Titre : chapitre » -> « Titre - chapitre »
    name = _INVALID.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    name = name[:120].rstrip(". ")
    if not name or name.upper().split(".")[0] in _RESERVED:
        return default
    return name


def ordered_files(planches: list[Planche], folder: Path) -> list[Path]:
    files = []
    for p in sorted(planches, key=lambda p: p.index):
        if not p.filename or not (folder / p.filename).is_file():
            raise OffreaderError(f"Planche {p.index} absente du dossier {folder}.")
        files.append(folder / p.filename)
    return files


def write_cbz(files: list[Path], target: Path) -> Path:
    """Crée l'archive sans recompression (ZIP_STORED) puis la vérifie."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    validate_cbz(tmp, [f.name for f in files])
    os.replace(tmp, target)
    return target


def validate_cbz(path: Path, expected_names: list[str]) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise OffreaderError(f"Archive CBZ invalide : {exc}") from exc
    if bad:
        raise OffreaderError(f"Archive CBZ corrompue (fichier {bad}).")
    if names != expected_names:
        raise OffreaderError("Le contenu de l'archive CBZ ne correspond pas aux planches attendues.")
