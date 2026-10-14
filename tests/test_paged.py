"""Stratégie B : lecteurs page par page (boutons, clavier, URL, liste, fin de chapitre)."""

import time
import zipfile

from offreader.models import Mode

from .conftest import expected_hashes, sha
from .server import HASHED, PAGES


def hashes_of(result):
    with zipfile.ZipFile(result.output) as zf:
        assert zf.testzip() is None
        return [sha(zf.read(n)) for n in zf.namelist()]


def test_button_reader_with_counter(runner):
    result, events = runner("/sites/paged_button.html")
    ex = result.extraction
    assert ex.mode is Mode.PAGED
    assert ex.expected_total == 6
    assert [p.page_label for p in ex.planches] == ["1", "2", "3", "4", "5", "6"]
    assert hashes_of(result) == expected_hashes(HASHED)
    assert "dernière page" in ex.end_reason
    # Le lien « Chapitre suivant » n'a jamais été suivi.
    assert all("vertical" not in p.url for p in ex.planches)
    assert any(e.kind == "pages" and e.current == 6 and e.total == 6 for e in events)


def test_keyboard_js_state_reader(runner):
    result, _ = runner("/sites/paged_keyboard.html")
    assert hashes_of(result) == expected_hashes(["zz03.jpg", "k7f2.jpg", "x6y1.jpg", "a91c.jpg"])


def test_click_reader_that_loops_stops(runner):
    result, _ = runner("/sites/paged_click_loop.html")
    assert hashes_of(result) == expected_hashes(["m4q8.jpg", "b0d5.jpg", "a91c.jpg"])
    assert "déjà vue" in result.extraction.end_reason


def test_select_reader(runner):
    result, _ = runner("/sites/paged_select.html")
    assert hashes_of(result) == expected_hashes(["b0d5.jpg", "zz03.jpg", "k7f2.jpg", "m4q8.jpg"])
    assert result.extraction.expected_total == 4


def test_link_reader_stops_at_counter(runner, server):
    result, _ = runner("/paged/1/1")
    assert hashes_of(result) == expected_hashes(PAGES[:5])
    assert server.state["hits"]["/paged/2/1"] == 0


def test_link_reader_does_not_follow_next_chapter(runner, server):
    result, _ = runner("/paged-trap/1/1")
    assert len(result.extraction.planches) == 5
    assert "quitté le chapitre" in result.extraction.end_reason


def test_url_only_reader_ends_on_404(runner):
    result, _ = runner("/query?page=1")
    assert hashes_of(result) == expected_hashes(HASHED[:4])


def test_stuck_reader_terminates(runner):
    start = time.monotonic()
    result, _ = runner("/sites/paged_stuck.html")
    assert len(result.extraction.planches) == 1
    assert time.monotonic() - start < 30


def test_infinite_reader_capped_by_max_pages(runner):
    result, _ = runner("/sites/paged_infinite.html", max_pages=7)
    assert len(result.extraction.planches) == 7
    assert "maximal" in result.extraction.end_reason
