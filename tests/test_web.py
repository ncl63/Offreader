"""Version en ligne : plusieurs liens d'un coup, file, téléchargement, annulation, reprise, mot de passe."""

import io
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from offreader.urls import parse_urls  # noqa: E402
from offreader.web import Settings, create_app  # noqa: E402

from .conftest import expected_hashes, fast_config, sha  # noqa: E402
from .server import PAGES  # noqa: E402


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(password="", max_parallel=2, **overrides):
        settings = Settings(data_dir=tmp_path / "data", password=password, max_parallel=max_parallel)
        app = create_app(settings, config_factory=lambda: fast_config(tmp_path, **overrides))
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def wait_jobs(client, until, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        jobs = client.get("/api/jobs").json()["jobs"]
        if until(jobs):
            return jobs
        time.sleep(0.2)
    raise AssertionError(f"délai dépassé : {[(j['status'], j['step']) for j in jobs]}")


def finished(jobs):
    return all(j["status"] not in ("queued", "running") for j in jobs)


def test_parse_urls():
    urls, rejected = parse_urls(" https://a.fr/1\n\nhttps://a.fr/2  https://a.fr/1\n<https://b.fr/x>\nfoo\n")
    assert urls == ["https://a.fr/1", "https://a.fr/2", "https://b.fr/x"]
    assert rejected == ["foo"]


def test_index_and_health(make_client):
    client = make_client()
    assert "Offreader" in client.get("/").text
    assert client.get("/healthz").json()["ok"]


def test_several_links_at_once(make_client, server):
    client = make_client(max_parallel=2)
    text = "\n".join([server.url("/sites/vertical.html"), server.url("/sites/paged_button.html"),
                      server.url("/sites/empty.html"), "pas-un-lien"])
    r = client.post("/api/jobs", json={"urls": text, "format": "cbz"})
    assert r.status_code == 201
    assert len(r.json()["jobs"]) == 3 and r.json()["rejected"] == ["pas-un-lien"]

    jobs = {j["url"]: j for j in wait_jobs(client, finished)}
    vertical = jobs[server.url("/sites/vertical.html")]
    assert vertical["status"] == "done" and vertical["count"] == 8 and vertical["name"].endswith(".cbz")
    assert jobs[server.url("/sites/paged_button.html")]["status"] == "done"
    empty = jobs[server.url("/sites/empty.html")]
    assert empty["status"] == "error" and "planche" in empty["message"].lower() and empty["download"] is None

    data = client.get(vertical["download"]).content
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert [sha(zf.read(n)) for n in zf.namelist()] == expected_hashes(PAGES[:8])

    archive = client.get("/api/archive").content
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert sorted(zf.namelist()) == sorted(j["name"] for j in jobs.values() if j["status"] == "done")


def test_images_format_served_as_zip(make_client, server):
    client = make_client()
    client.post("/api/jobs", json={"urls": [server.url("/sites/vertical.html")], "format": "images"})
    (job,) = wait_jobs(client, finished)
    assert job["status"] == "done"
    r = client.get(job["download"])
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert len(names) == 8 and names[0].startswith("001.") and not any(n.startswith(".") for n in names)


def test_queue_respects_parallel_limit(make_client, server):
    client = make_client(max_parallel=1)
    urls = [server.url("/sites/slow.html"), server.url("/sites/vertical.html")]
    client.post("/api/jobs", json={"urls": urls, "format": "cbz"})
    seen_running = []
    jobs = wait_jobs(client, lambda js: seen_running.append(sum(j["status"] == "running" for j in js)) or finished(js))
    assert max(seen_running) == 1
    assert [j["status"] for j in jobs] == ["done", "done"]


def test_cancel_then_resume_then_delete(make_client, server, tmp_path):
    client = make_client(workers=1)
    (job,) = client.post("/api/jobs", json={"urls": server.url("/sites/slow.html")}).json()["jobs"]
    wait_jobs(client, lambda js: js[0]["step"] == "Téléchargement..." and js[0]["current"] >= 2)
    client.post(f"/api/jobs/{job['id']}/cancel")
    (job,) = wait_jobs(client, finished, timeout=30)
    assert job["status"] == "cancelled"

    client.post(f"/api/jobs/{job['id']}/retry")
    (job,) = wait_jobs(client, finished)
    assert job["status"] == "done" and job["count"] == 8

    assert client.delete(f"/api/jobs/{job['id']}").json()["ok"]
    assert client.get("/api/jobs").json()["jobs"] == []
    assert not any((tmp_path / "data" / "jobs").iterdir())


def test_rejects_bad_input(make_client):
    client = make_client()
    assert client.post("/api/jobs", json={"urls": "rien"}).status_code == 422
    assert client.post("/api/jobs", json={"urls": "https://a.fr", "format": "pdf"}).status_code == 422
    assert client.get("/api/jobs/inconnu/file").status_code == 404


def test_password(make_client):
    client = make_client(password="s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/jobs", auth=("x", "faux")).status_code == 401
    assert client.get("/api/jobs", auth=("x", "s3cret")).status_code == 200
