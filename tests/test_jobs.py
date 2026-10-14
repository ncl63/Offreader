"""Exécution en arrière-plan (utilisée par l'interface graphique) : progression, erreurs, annulation, reprise."""

import time
import zipfile

from offreader.jobs import DownloadJob

from .conftest import expected_hashes, fast_config, sha
from .server import PAGES


def wait_messages(job, until, timeout=120):
    """Collecte les messages jusqu'à ce que `until(msgs)` soit vrai ou que le travail se termine."""
    msgs, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        msgs += job.poll()
        if until(msgs) or (not job.running and job.messages.empty()):
            msgs += job.poll()
            return msgs
        time.sleep(0.05)
    raise AssertionError(f"délai dépassé ; messages : {[m.kind for m in msgs]}")


def final(msgs):
    return [m for m in msgs if m.kind != "progress"]


def test_job_success_reports_progress_and_output(server, tmp_path):
    job = DownloadJob(server.url("/sites/vertical.html"), fast_config(tmp_path)).start()
    msgs = wait_messages(job, lambda m: bool(final(m)))
    assert job.join(10)
    end = final(msgs)
    assert [m.kind for m in end] == ["done"]
    assert end[0].count == 8 and end[0].output.is_file()
    kinds = {m.event.kind for m in msgs if m.kind == "progress"}
    assert {"analysis", "mode", "pages", "download", "export"} <= kinds


def test_job_error_is_reported_not_raised(server, tmp_path):
    job = DownloadJob(server.url("/sites/empty.html"), fast_config(tmp_path)).start()
    end = final(wait_messages(job, lambda m: bool(final(m))))
    assert end[0].kind == "error" and "planche" in end[0].text.lower()


def test_job_incomplete_is_reported(server, tmp_path):
    job = DownloadJob(server.url("/sites/gone.html"), fast_config(tmp_path, max_retries=0)).start()
    end = final(wait_messages(job, lambda m: bool(final(m))))
    assert end[0].kind == "incomplete" and "planche 3" in end[0].text


def test_cancel_during_extraction_stops_quickly(server, tmp_path):
    job = DownloadJob(server.url("/sites/paged_infinite.html"), fast_config(tmp_path)).start()
    wait_messages(job, lambda m: any(x.kind == "progress" and x.event.kind == "pages" and x.event.current >= 3 for x in m))
    start = time.monotonic()
    job.cancel()
    end = final(wait_messages(job, lambda m: bool(final(m)), timeout=30))
    assert end[0].kind == "cancelled"
    assert job.join(10)
    assert time.monotonic() - start < 15


def test_cancel_during_download_then_resume(server, tmp_path):
    cfg = fast_config(tmp_path, workers=1)
    job = DownloadJob(server.url("/sites/slow.html"), cfg).start()
    wait_messages(job, lambda m: any(x.kind == "progress" and x.event.kind == "download" and x.event.current >= 2 for x in m))
    job.cancel()
    end = final(wait_messages(job, lambda m: bool(final(m)), timeout=30))
    assert end[0].kind == "cancelled"
    parts = tmp_path / "Lecteur lent.parts"
    kept = sorted(f.name for f in parts.iterdir() if not f.name.startswith("."))
    assert 2 <= len(kept) < 8  # téléchargement réellement interrompu, planches valides conservées

    # Reprise : seules les planches manquantes sont retéléchargées.
    job = DownloadJob(server.url("/sites/slow.html"), fast_config(tmp_path, workers=4)).start()
    end = final(wait_messages(job, lambda m: bool(final(m))))
    assert end[0].kind == "done"
    with zipfile.ZipFile(end[0].output) as zf:
        assert [sha(zf.read(n)) for n in zf.namelist()] == expected_hashes(PAGES[:8])


def test_cancel_before_start(server, tmp_path):
    job = DownloadJob(server.url("/sites/vertical.html"), fast_config(tmp_path))
    job.cancel()
    job.start()
    end = final(wait_messages(job, lambda m: bool(final(m))))
    assert end[0].kind == "cancelled"
    assert not any(tmp_path.iterdir())
