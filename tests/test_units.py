"""Tests unitaires : URL, contrôle d'intégrité, nommage, CBZ."""

import zipfile

import pytest

from offreader.downloader import inspect_image
from offreader.errors import CorruptImage, OffreaderError
from offreader.exporter import safe_name, validate_cbz, write_cbz
from offreader.strategies.paged import next_page_url, url_change_kind

from .server import IMAGES


def test_next_page_url():
    assert next_page_url("http://s/read?id=12&page=3", 3) == "http://s/read?id=12&page=4"
    assert next_page_url("http://s/manga/chapter-12/page-3", 3) == "http://s/manga/chapter-12/page-4"
    assert next_page_url("http://s/c/12#5", 5) == "http://s/c/12#6"
    assert next_page_url("http://s/chapter-1", 1) is None  # jamais le numéro de chapitre
    assert next_page_url("http://s/read?page=3", 2) is None


def test_url_change_kind():
    assert url_change_kind("http://s/a/1/2", "http://s/a/1/2") == "same"
    assert url_change_kind("http://s/a/1/2", "http://s/a/1/3") == "page"
    assert url_change_kind("http://s/a/1/5", "http://s/a/2/1") == "other"
    assert url_change_kind("http://s/r?page=1", "http://s/r?page=2") == "page"
    assert url_change_kind("http://s/r", "http://autre/r") == "other"


@pytest.mark.parametrize("name,ext", [("p01.jpg", "jpg"), ("p02.webp", "webp"), ("p03.png", "png")])
def test_inspect_image_formats(name, ext):
    assert inspect_image(IMAGES[name]) == ext


def test_inspect_image_rejects_truncated_and_html():
    data = IMAGES["p01.jpg"]
    with pytest.raises(CorruptImage):
        inspect_image(data[: len(data) // 2])
    with pytest.raises(CorruptImage):
        inspect_image(b"<html>Erreur 403</html>", "text/html")
    with pytest.raises(CorruptImage):
        inspect_image(b"")


def test_safe_name_windows():
    assert safe_name('Série: "Test" / Ch. 1?') == "Série_ _Test_ _ Ch. 1_"
    assert safe_name("CON") == "chapitre"
    assert safe_name("  fin...  ") == "fin"
    assert safe_name("") == "chapitre"


def test_write_cbz_is_stored_and_ordered(tmp_path):
    files = []
    for i, name in enumerate(["p01.jpg", "p02.webp", "p03.png"], 1):
        f = tmp_path / f"{i:03d}.{name.split('.')[1]}"
        f.write_bytes(IMAGES[name])
        files.append(f)
    cbz = write_cbz(files, tmp_path / "out" / "test.cbz")
    with zipfile.ZipFile(cbz) as zf:
        assert zf.namelist() == ["001.jpg", "002.webp", "003.png"]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
        assert zf.read("002.webp") == IMAGES["p02.webp"]
    with pytest.raises(OffreaderError):
        validate_cbz(cbz, ["001.jpg"])
