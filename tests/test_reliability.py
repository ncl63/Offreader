"""Erreurs réseau, fichiers incomplets, reprise, CLI et codes de sortie."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from offreader.errors import IncompleteChapter
from offreader.models import Status

from .conftest import expected_hashes, sha
from .server import PAGES

ROOT = Path(__file__).resolve().parent.parent


def test_transient_errors_are_retried(runner, server):
    result, _ = runner("/sites/errors.html")
    with zipfile.ZipFile(result.output) as zf:
        assert [sha(zf.read(n)) for n in zf.namelist()] == expected_hashes(PAGES[:5])
    d = server.state["downloads"]
    assert d["/flaky/p02.webp"] == 3  # 2 × 503 puis succès
    assert d["/trunc/p03.png"] == 2  # connexion coupée puis succès
    assert d["/corrupt/p04.jpg"] == 2  # image tronquée détectée par Pillow puis succès


def test_missing_page_blocks_cbz_then_resume(runner, server, tmp_path):
    with pytest.raises(IncompleteChapter) as err:
        runner("/sites/gone.html", max_retries=1)
    assert "planche 3" in str(err.value)
    parts = tmp_path / "Planche manquante.parts"
    assert sorted(f.name for f in parts.iterdir() if not f.name.startswith(".")) == ["001.jpg", "002.webp", "004.jpg"]
    assert not (tmp_path / "Planche manquante.cbz").exists()  # jamais de CBZ incomplet

    # Le serveur se rétablit : la reprise ne retélécharge que la planche manquante.
    server.state["gone_fixed"] = True
    server.state["downloads"].clear()
    result, _ = runner("/sites/gone.html")
    statuses = [p.status for p in result.extraction.planches]
    assert statuses == [Status.SKIPPED, Status.SKIPPED, Status.DOWNLOADED, Status.SKIPPED]
    assert dict(server.state["downloads"]) == {"/gone/p03.png": 1}
    with zipfile.ZipFile(result.output) as zf:
        assert [sha(zf.read(n)) for n in zf.namelist()] == expected_hashes(["p01.jpg", "p02.webp", "p03.png", "p04.jpg"])


def test_resume_detects_damaged_file(runner, server, tmp_path):
    runner("/sites/vertical.html", format="images")
    folder = tmp_path / "Série Test - Chapitre 1"
    victim = folder / "003.png"
    victim.write_bytes(victim.read_bytes()[:100])  # fichier abîmé entre deux exécutions
    (folder / "999.jpg").write_bytes(b"obsolete")  # fichier étranger à l'ordre de lecture
    server.state["downloads"].clear()
    result, _ = runner("/sites/vertical.html", format="images")
    assert dict(server.state["downloads"]) == {"/img/p03.png": 1}
    files = sorted(f.name for f in folder.iterdir() if not f.name.startswith("."))
    assert len(files) == 8 and "999.jpg" not in files
    assert [sha((folder / f).read_bytes()) for f in files] == expected_hashes(PAGES[:8])


def _cli(server, tmp_path, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "manga_downloader.py"), *args, "--output", str(tmp_path), "--timeout", "10"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=180, cwd=ROOT,
    )


def test_cli_success(server, tmp_path):
    proc = _cli(server, tmp_path, server.url("/sites/paged_button.html"), "--format", "cbz", "--name", "Mon chapitre")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    for expected in ("Analyse du lecteur...", "Mode détecté : page par page", "Pages récupérées : 6 / 6",
                     "Téléchargement : 100 %", "Export CBZ...", "Terminé"):
        assert expected in out
    assert (tmp_path / "Mon chapitre.cbz").is_file()


def test_cli_error_exit_codes(server, tmp_path):
    proc = _cli(server, tmp_path, server.url("/sites/empty.html"))
    assert proc.returncode == 1
    assert "Erreur :" in proc.stdout
    proc = _cli(server, tmp_path, server.url("/sites/gone.html"), "--retries", "0")
    assert proc.returncode == 2
    assert "Incomplet" in proc.stdout and "relancez" in proc.stdout
    proc = _cli(server, tmp_path, server.url("/n-existe-pas"))
    assert proc.returncode == 1
    assert "404" in proc.stdout


def test_cli_several_links(server, tmp_path):
    links = tmp_path / "liens.txt"
    links.write_text(server.url("/sites/paged_button.html") + "\n\n" + server.url("/sites/empty.html") + "\n",
                     encoding="utf-8")
    proc = _cli(server, tmp_path, server.url("/sites/vertical.html"), "--file", str(links))
    assert proc.returncode == 1, proc.stdout + proc.stderr  # un des trois liens échoue
    assert "[1/3]" in proc.stdout and "[3/3]" in proc.stdout
    assert "Bilan : 2 / 3 chapitre(s) terminé(s)" in proc.stdout
    assert len(list(tmp_path.glob("*.cbz"))) == 2
