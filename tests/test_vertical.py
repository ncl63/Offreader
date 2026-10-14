"""Stratégie A : lecteurs à défilement vertical (moteur complet, navigateur réel)."""

import zipfile

import pytest

from offreader.errors import NoImagesFound
from offreader.models import Mode, Status

from .conftest import expected_hashes, sha
from .server import PAGES


def cbz_hashes(path):
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        return names, [sha(zf.read(n)) for n in names]


def test_vertical_classic_cbz(runner, tmp_path):
    result, events = runner("/sites/vertical.html")
    assert result.extraction.mode is Mode.VERTICAL
    assert result.output == tmp_path / "Série Test - Chapitre 1.cbz"
    names, hashes = cbz_hashes(result.output)
    assert names == ["001.jpg", "002.webp", "003.png", "004.jpg", "005.jpg", "006.webp", "007.png", "008.jpg"]
    assert hashes == expected_hashes(PAGES[:8])  # ordre exact, octets d'origine
    assert not (tmp_path / "Série Test - Chapitre 1.parts").exists()
    assert any(e.kind == "mode" and e.message == "défilement vertical" for e in events)


def test_lazy_loading_and_infinite_append(runner, tmp_path):
    result, _ = runner("/sites/lazy.html", format="images")
    folder = result.output
    files = sorted(f.name for f in folder.iterdir() if not f.name.startswith("."))
    assert len(files) == 10
    assert [sha((folder / f).read_bytes()) for f in files] == expected_hashes(PAGES[:10])


def test_parasites_excluded_and_distinct_duplicates_kept(runner):
    result, _ = runner("/sites/parasites.html")
    urls = [p.url.split("/")[-1] for p in result.extraction.planches]
    # Logo, pixel, bannière, pub, miniatures, avatar, image cachée : exclus.
    # p03 en double (même URL) : une seule fois. blank-a / blank-b (même contenu,
    # URL différentes) : deux planches distinctes, conservées.
    assert urls == ["p01.jpg", "p02.webp", "blank-a.jpg", "blank-b.jpg", "p03.png", "p04.jpg", "p05.jpg"]
    names, hashes = cbz_hashes(result.output)
    assert len(names) == 7
    assert hashes[2] == hashes[3]


def test_inner_scroll_container(runner):
    result, _ = runner("/sites/inner_scroll.html")
    names, hashes = cbz_hashes(result.output)
    assert hashes == expected_hashes(PAGES[:6])


def test_no_images_is_a_clear_error(runner):
    with pytest.raises(NoImagesFound):
        runner("/sites/empty.html")


def test_forced_vertical_mode(runner):
    result, _ = runner("/sites/vertical.html", mode="vertical")
    assert len(result.extraction.planches) == 8
    assert all(p.status is Status.DOWNLOADED for p in result.extraction.planches)
