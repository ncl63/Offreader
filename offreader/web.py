"""Version en ligne : serveur web (FastAPI) avec file de téléchargements.

On colle un ou plusieurs liens dans la page ; chaque lien devient un travail
mis en file. Au plus `OFFREADER_MAX_PARALLEL` chapitres sont traités en même
temps (chacun ouvre son propre Chromium). Les fichiers produits restent sur le
serveur `OFFREADER_RETENTION_HOURS` heures, puis sont supprimés.

Variables d'environnement :
  PORT                        port d'écoute (défaut 8000)
  OFFREADER_PASSWORD          mot de passe demandé par le navigateur (fortement conseillé)
  OFFREADER_DATA_DIR          dossier des fichiers produits (défaut ./offreader-data)
  OFFREADER_MAX_PARALLEL      chapitres traités simultanément (défaut 2)
  OFFREADER_RETENTION_HOURS   durée de conservation des fichiers (défaut 24)
  OFFREADER_MAX_URLS          liens acceptés par envoi (défaut 50)

Lancement : python -m offreader.web
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import secrets
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__
from .config import Config
from .engine import run
from .errors import IncompleteChapter, OffreaderError
from .models import ProgressEvent
from .urls import parse_urls

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
FINISHED = {"done", "incomplete", "error", "cancelled"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("OFFREADER_DATA_DIR", "offreader-data")))
    password: str = field(default_factory=lambda: os.environ.get("OFFREADER_PASSWORD", ""))
    max_parallel: int = field(default_factory=lambda: _env_int("OFFREADER_MAX_PARALLEL", 2))
    retention_hours: int = field(default_factory=lambda: _env_int("OFFREADER_RETENTION_HOURS", 24))
    max_urls: int = field(default_factory=lambda: _env_int("OFFREADER_MAX_URLS", 50))


@dataclass
class Job:
    id: str
    url: str
    format: str
    folder: Path
    status: str = "queued"  # queued | running | done | incomplete | error | cancelled
    step: str = "En attente..."
    mode: str = ""
    current: int = 0
    total: int = 0
    message: str = ""
    output: Path | None = None
    count: int = 0
    created: float = field(default_factory=time.time)
    finished: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict:
        downloadable = self.output is not None and self.output.exists() and self.status in ("done", "incomplete")
        return {
            "id": self.id, "url": self.url, "format": self.format, "status": self.status,
            "step": self.step, "mode": self.mode, "current": self.current, "total": self.total,
            "message": self.message, "count": self.count, "created": self.created,
            "name": self.output.name if self.output else "",
            "download": f"api/jobs/{self.id}/file" if downloadable and self.status == "done" else None,
        }


class JobManager:
    """File de travaux exécutés dans la boucle asyncio du serveur."""

    def __init__(self, settings: Settings, config_factory=Config):
        self.settings = settings
        self.config_factory = config_factory
        self.jobs: dict[str, Job] = {}
        self._slots: asyncio.Semaphore | None = None

    @property
    def slots(self) -> asyncio.Semaphore:
        if self._slots is None:  # créé dans la boucle du serveur
            self._slots = asyncio.Semaphore(self.settings.max_parallel)
        return self._slots

    def add(self, url: str, fmt: str) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id, url, fmt, self.settings.data_dir / "jobs" / job_id)
        self.jobs[job_id] = job
        self._start(job)
        return job

    def _start(self, job: Job) -> None:
        job.status, job.step, job.message, job.finished = "queued", "En attente...", "", None
        job.current = job.total = 0
        job.task = asyncio.get_running_loop().create_task(self._run(job))

    def retry(self, job: Job) -> None:
        """Relance dans le même dossier : seules les planches manquantes sont retéléchargées."""
        if job.status not in FINISHED:
            raise HTTPException(409, "Ce téléchargement est encore en cours.")
        self._start(job)

    def cancel(self, job: Job) -> None:
        if job.task and not job.task.done():
            job.task.cancel()

    async def remove(self, job: Job) -> None:
        self.cancel(job)
        if job.task:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(job.task), 20)
        self.jobs.pop(job.id, None)
        shutil.rmtree(job.folder, ignore_errors=True)

    def _on_event(self, job: Job, e: ProgressEvent) -> None:
        if e.kind == "mode":
            job.mode = e.message
        elif e.kind == "pages":
            job.step, job.current, job.total = "Analyse du lecteur...", e.current, e.total
        elif e.kind == "download":
            job.step, job.current, job.total = "Téléchargement...", e.current, e.total
        elif e.kind == "warning":
            job.message = e.message
        else:
            job.step = e.message

    async def _run(self, job: Job) -> None:
        try:
            async with self.slots:
                job.status, job.step = "running", "Analyse du lecteur..."
                config = self.config_factory()
                config.output_dir = job.folder
                config.format = job.format
                config.headless = True
                job.folder.mkdir(parents=True, exist_ok=True)
                result = await run(job.url, config, lambda e: self._on_event(job, e))
        except asyncio.CancelledError:
            job.status, job.step = "cancelled", "Annulé"
            job.message = "Téléchargement annulé. « Reprendre » repart là où il s'était arrêté."
        except IncompleteChapter as exc:
            job.status, job.step, job.message = "incomplete", "Incomplet", str(exc)
        except OffreaderError as exc:
            job.status, job.step, job.message = "error", "Échec", str(exc)
        except Exception as exc:  # jamais de travail qui meurt en silence
            log.exception("Erreur inattendue sur %s", job.url)
            job.status, job.step = "error", "Échec"
            job.message = f"Erreur inattendue : {type(exc).__name__}: {exc}"
        else:
            job.status, job.step = "done", "Terminé"
            job.output, job.count = result.output, len(result.extraction.planches)
            job.current = job.total = job.count
        finally:
            job.finished = time.time()
            # Les chemins internes du serveur n'intéressent pas l'utilisateur.
            job.message = job.message.replace(str(job.folder) + os.sep, "").replace(str(job.folder), "le serveur")

    def cleanup(self) -> None:
        """Supprime les travaux terminés trop anciens et les dossiers orphelins."""
        limit = time.time() - self.settings.retention_hours * 3600
        for job in list(self.jobs.values()):
            if job.status in FINISHED and (job.finished or job.created) < limit:
                self.jobs.pop(job.id, None)
                shutil.rmtree(job.folder, ignore_errors=True)
        root = self.settings.data_dir / "jobs"
        if root.is_dir():
            for folder in root.iterdir():
                if folder.name not in self.jobs and folder.stat().st_mtime < limit:
                    shutil.rmtree(folder, ignore_errors=True)


def _zip_folder(folder: Path, target: Path, prefix: str = "") -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in sorted(folder.iterdir()):
            if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".part"):
                zf.write(f, arcname=f"{prefix}{f.name}")


def job_file(job: Job) -> Path:
    """Fichier à servir : le CBZ, ou une archive ZIP du dossier d'images."""
    if job.output is None or not job.output.exists():
        raise HTTPException(404, "Fichier introuvable (expiré ou supprimé).")
    if job.output.is_file():
        return job.output
    archive = job.folder / f"{job.output.name}.zip"
    if not archive.is_file():
        tmp = archive.with_name(archive.name + ".part")
        _zip_folder(job.output, tmp)
        os.replace(tmp, archive)
    return archive


class NewJobs(BaseModel):
    urls: str | list[str]
    format: str = "cbz"


def create_app(settings: Settings | None = None, config_factory=Config) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(settings, config_factory)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async def janitor():
            while True:
                with contextlib.suppress(Exception):
                    manager.cleanup()
                await asyncio.sleep(600)

        task = asyncio.create_task(janitor())
        if not settings.password:
            log.warning("OFFREADER_PASSWORD n'est pas défini : le site est accessible à tous.")
        try:
            yield
        finally:
            task.cancel()
            for job in manager.jobs.values():
                manager.cancel(job)

    app = FastAPI(title="Offreader", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.manager = manager

    @app.middleware("http")
    async def password_guard(request: Request, call_next):
        if settings.password and request.url.path != "/healthz":
            ok = False
            scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() == "basic":
                with contextlib.suppress(Exception):
                    _, _, given = base64.b64decode(encoded).decode("utf-8").partition(":")
                    ok = secrets.compare_digest(given.encode(), settings.password.encode())
            if not ok:
                return Response("Mot de passe requis.", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Offreader", charset="UTF-8"'})
        return await call_next(request)

    def get_job(job_id: str) -> Job:
        job = manager.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Téléchargement introuvable.")
        return job

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/api/jobs")
    async def list_jobs():
        jobs = sorted(manager.jobs.values(), key=lambda j: j.created, reverse=True)
        return {"jobs": [j.public() for j in jobs], "max_urls": settings.max_urls,
                "retention_hours": settings.retention_hours}

    @app.post("/api/jobs")
    async def add_jobs(body: NewJobs):
        if body.format not in ("cbz", "images"):
            raise HTTPException(422, "Format inconnu (cbz ou images).")
        text = body.urls if isinstance(body.urls, str) else "\n".join(body.urls)
        urls, rejected = parse_urls(text)
        if not urls:
            raise HTTPException(422, "Collez au moins une adresse commençant par http:// ou https://")
        if len(urls) > settings.max_urls:
            raise HTTPException(422, f"Trop de liens d'un coup ({len(urls)}) : {settings.max_urls} au maximum.")
        jobs = [manager.add(u, body.format) for u in urls]
        return JSONResponse({"jobs": [j.public() for j in jobs], "rejected": rejected}, status_code=201)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        job = get_job(job_id)
        manager.cancel(job)
        return job.public()

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(job_id: str):
        job = get_job(job_id)
        manager.retry(job)
        return job.public()

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str):
        await manager.remove(get_job(job_id))
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/file")
    async def download(job_id: str):
        job = get_job(job_id)
        if job.status != "done":
            raise HTTPException(409, "Ce téléchargement n'est pas terminé.")
        path = await asyncio.to_thread(job_file, job)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.get("/api/archive")
    async def archive(ids: str = ""):
        """Tous les chapitres terminés demandés (ou tous), dans une seule archive ZIP."""
        wanted = [i for i in ids.split(",") if i] or list(manager.jobs)
        jobs = [manager.jobs[i] for i in wanted if i in manager.jobs and manager.jobs[i].status == "done"]
        if not jobs:
            raise HTTPException(404, "Aucun chapitre terminé à télécharger.")

        def build() -> Path:
            target = settings.data_dir / f"offreader-{uuid.uuid4().hex[:8]}.zip"
            used: set[str] = set()
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as zf:
                for job in jobs:
                    if job.output is None or not job.output.exists():
                        continue
                    name, n = job.output.name, 2
                    while name in used:  # deux chapitres au même titre
                        name, n = f"{job.output.stem} ({n}){job.output.suffix}", n + 1
                    used.add(name)
                    if job.output.is_file():
                        zf.write(job.output, arcname=name)
                    else:
                        for f in sorted(job.output.iterdir()):
                            if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".part"):
                                zf.write(f, arcname=f"{name}/{f.name}")
            return target

        path = await asyncio.to_thread(build)

        async def remove_later():
            await asyncio.sleep(3600)
            path.unlink(missing_ok=True)

        asyncio.get_running_loop().create_task(remove_later())
        return FileResponse(path, filename="offreader.zip", media_type="application/zip")

    return app


def main() -> None:
    import uvicorn

    from .browser import use_bundled_browsers

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    use_bundled_browsers()
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"), port=port, proxy_headers=True,
                forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
