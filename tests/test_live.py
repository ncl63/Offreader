"""Validation sur un vrai site (réseau requis) : désactivée par défaut.

    set OFFREADER_LIVE=1        (PowerShell : $env:OFFREADER_LIVE = "1")
    python -m pytest tests/test_live.py -v -s

Le nombre de planches attendu n'est pas codé en dur : on vérifie l'ordre, la
cohérence et l'absence de parasites, et on affiche le décompte à comparer avec
l'inspection manuelle (~51 pages numérotées pour ce chapitre).
"""

import asyncio
import io
import os
import re
import zipfile

import pytest
from PIL import Image

from offreader.config import Config
from offreader.engine import run
from offreader.models import Mode

BORUTO = "https://boruto-france.fr/boruto-blue-vortex-chapitre-01-fr/"

pytestmark = pytest.mark.skipif(os.environ.get("OFFREADER_LIVE") != "1", reason="test réseau (OFFREADER_LIVE=1)")


def page_number(url: str) -> int:
    """Numéro de planche au début du nom de fichier WordPress (« 12-7-714x1024-1.webp » -> 12)."""
    return int(re.match(r"(\d+)", url.rsplit("/", 1)[-1]).group(1))


def test_boruto_blue_vortex_chapter_1(tmp_path):
    result = asyncio.run(run(BORUTO, Config(output_dir=tmp_path), lambda e: None))
    ex = result.extraction
    urls = [p.url for p in ex.planches]
    print(f"\nMode : {ex.mode.label} ; planches : {len(urls)}")
    assert ex.mode is Mode.VERTICAL
    assert len(urls) >= 45
    # Ordre d'apparition : les numéros de planche sont strictement croissants, sans trou.
    numbers = [page_number(u) for u in urls]
    assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
    covered = set()
    for u in urls:
        name = u.rsplit("/", 1)[-1]
        double = re.match(r"(\d+)-(\d+)-\d+x\d+", name)  # double page : « 1-2-1024x735-1.jpg »
        if double and int(double.group(2)) == int(double.group(1)) + 1:
            covered |= {int(double.group(1)), int(double.group(2))}
        else:
            covered.add(page_number(u))
    print(f"Pages numérotées couvertes : {min(covered)} à {max(covered)} ({len(covered)})")
    assert set(range(1, max(covered) + 1)) <= covered, "planche manquante dans la numérotation"
    # Parasites : logo, image mise en avant, miniatures « articles similaires ».
    assert not [u for u in urls if re.search(r"logo|310x165|660x330", u)]
    with zipfile.ZipFile(result.output) as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        assert names == sorted(names) and len(names) == len(urls)
        for n in names:
            with Image.open(io.BytesIO(zf.read(n))) as img:
                img.load()
                assert min(img.size) >= 500  # planches pleine taille
