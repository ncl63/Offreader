"""Lecture d'une liste de liens collée par l'utilisateur (un ou plusieurs chapitres)."""

from __future__ import annotations

import re

_SEPARATORS = re.compile(r"\s+")


def parse_urls(text: str) -> tuple[list[str], list[str]]:
    """Découpe `text` (un lien par ligne, ou séparés par des espaces).

    Renvoie (liens valides sans doublon, dans l'ordre ; morceaux refusés).
    """
    urls: list[str] = []
    rejected: list[str] = []
    for token in _SEPARATORS.split(text or ""):
        token = token.strip().strip("<>\"'")
        if not token:
            continue
        if token.lower().startswith(("http://", "https://")) and len(token) > 8:
            if token not in urls:
                urls.append(token)
        else:
            rejected.append(token)
    return urls, rejected
