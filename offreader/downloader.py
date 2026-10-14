"""Téléchargement parallèle, vérifié et reprenable des planches."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Callable
from urllib.parse import unquote_to_bytes

import httpx
from PIL import Image, UnidentifiedImageError

from .config import Config
from .errors import CorruptImage, DownloadError
from .models import Planche, ProgressEvent, Status

log = logging.getLogger(__name__)

MANIFEST = ".offreader.json"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
PIL_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "AVIF": "avif",
           "BMP": "bmp", "TIFF": "tif", "JPEG2000": "jp2"}
MIME_EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp",
            "image/gif": "gif", "image/avif": "avif", "image/jxl": "jxl", "image/bmp": "bmp",
            "image/heic": "heic"}
IMAGE_FILE = re.compile(r"^\d+\.[a-z0-9]+$")

Image.MAX_IMAGE_PIXELS = None  # les webtoons dépassent la limite par défaut de Pillow


def inspect_image(data: bytes, content_type: str = "") -> str:
    """Vérifie que `data` est une image complète et renvoie l'extension d'origine."""
    if not data:
        raise CorruptImage("fichier vide")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()  # décode tout : détecte les fichiers tronqués
    except UnidentifiedImageError:
        # Format inconnu de Pillow (ex. JPEG XL) : on fait confiance au type MIME.
        mime = content_type.split(";")[0].strip().lower()
        if mime in MIME_EXT:
            log.debug("Format %s non vérifiable par Pillow, accepté sur la foi du type MIME", mime)
            return MIME_EXT[mime]
        raise CorruptImage(f"contenu non reconnu comme une image (type {mime or 'inconnu'})")
    except (OSError, SyntaxError, ValueError) as exc:
        raise CorruptImage(f"image incomplète ou corrompue ({exc})") from exc
    return PIL_EXT.get(fmt, (fmt or "img").lower())


def decode_data_url(url: str) -> bytes:
    header, _, payload = url.partition(",")
    if header.endswith(";base64"):
        return base64.b64decode(payload)
    return unquote_to_bytes(payload)


def build_cookies(cookies: list[dict]) -> httpx.Cookies:
    jar = httpx.Cookies()
    for c in cookies:
        jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
    return jar


class Downloader:
    def __init__(self, config: Config, cookies: list[dict] | None = None,
                 headers: dict[str, str] | None = None,
                 on_event: Callable[[ProgressEvent], None] | None = None):
        self.config = config
        self.cookies = build_cookies(cookies or [])
        self.headers = headers or {}
        self.on_event = on_event or (lambda e: None)
        self._lock = asyncio.Lock()
        self._done = 0

    async def download_all(self, planches: list[Planche], dest: Path, source_url: str = "") -> None:
        """Télécharge toutes les planches dans `dest` (noms 001.ext, 002.ext...).

        L'ordre est garanti par le nom de fichier, qui dépend uniquement de
        l'index de lecture, quel que soit l'ordre de fin des téléchargements.
        """
        dest.mkdir(parents=True, exist_ok=True)
        width = max(3, len(str(len(planches))))
        manifest = self._load_manifest(dest)
        self._cleanup(dest, planches, width, manifest)
        self._manifest = {"source": source_url, "pages": manifest}
        self._done = 0
        sem = asyncio.Semaphore(max(1, self.config.workers))
        timeout = httpx.Timeout(self.config.download_timeout)
        async with httpx.AsyncClient(headers=self.headers, cookies=self.cookies, timeout=timeout,
                                     follow_redirects=True) as client:
            async def worker(p: Planche):
                async with sem:
                    await self._download_one(client, p, dest, width)
                async with self._lock:
                    self._done += 1
                    self.on_event(ProgressEvent("download", "Téléchargement", self._done, len(planches)))

            await asyncio.gather(*(worker(p) for p in planches))

    # ------------------------------------------------------------------ interne

    async def _download_one(self, client: httpx.AsyncClient, p: Planche, dest: Path, width: int) -> None:
        stem = f"{p.index:0{width}d}"
        existing = self._existing(dest, stem, p)
        if existing:
            p.filename, p.size, p.status = existing.name, existing.stat().st_size, Status.SKIPPED
            return
        last_error = "erreur inconnue"
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                await asyncio.sleep(min(self.config.retry_base_delay * 2 ** (attempt - 1), 30))
            try:
                data, ctype = await self._fetch(client, p)
                ext = inspect_image(data, ctype)
            except _Retryable as exc:
                last_error = str(exc)
                log.debug("Planche %d, tentative %d : %s", p.index, attempt + 1, exc)
                continue
            except (DownloadError, CorruptImage) as exc:
                last_error = str(exc)
                if isinstance(exc, CorruptImage) and not p.url.startswith(("data:", "blob:")):
                    log.debug("Planche %d, tentative %d : %s", p.index, attempt + 1, exc)
                    continue  # un transfert interrompu peut réussir au prochain essai
                break
            await self._save(dest, stem, ext, data, p)
            return
        p.status, p.error = Status.FAILED, last_error
        log.warning("Planche %d (%s) : %s", p.index, p.url, last_error)

    async def _fetch(self, client: httpx.AsyncClient, p: Planche) -> tuple[bytes, str]:
        if p.data is not None:
            return p.data, ""
        if p.url.startswith("data:"):
            return decode_data_url(p.url), p.url[5:].split(";")[0].split(",")[0]
        try:
            response = await client.get(p.url)
        except httpx.TransportError as exc:  # délai, connexion coupée, transfert incomplet
            raise _Retryable(f"erreur réseau : {type(exc).__name__} {exc}".strip()) from exc
        if response.status_code in RETRY_STATUS:
            raise _Retryable(f"erreur HTTP {response.status_code} (temporaire)")
        if response.status_code >= 400:
            raise DownloadError(f"erreur HTTP {response.status_code}")
        data = response.content
        expected = response.headers.get("content-length")
        if expected and "content-encoding" not in response.headers and int(expected) != len(data):
            raise _Retryable(f"téléchargement incomplet ({len(data)}/{expected} octets)")
        return data, response.headers.get("content-type", "")

    async def _save(self, dest: Path, stem: str, ext: str, data: bytes, p: Planche) -> None:
        final = dest / f"{stem}.{ext}"
        part = dest / f".{stem}.part"
        part.write_bytes(data)
        os.replace(part, final)  # écriture atomique : jamais de fichier final à moitié écrit
        p.filename, p.size, p.status, p.error = final.name, len(data), Status.DOWNLOADED, None
        async with self._lock:
            self._manifest["pages"][stem] = {"url": p.url, "file": final.name,
                                             "sha256": hashlib.sha256(data).hexdigest()}
            (dest / MANIFEST).write_text(json.dumps(self._manifest, indent=1), encoding="utf-8")

    def _existing(self, dest: Path, stem: str, p: Planche) -> Path | None:
        """Reprise : fichier déjà téléchargé pour la même URL et toujours intact."""
        entry = self._manifest["pages"].get(stem)
        if not entry or entry.get("url") != p.url:
            return None
        path = dest / entry.get("file", "")
        if not path.is_file():
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            return None
        return path

    @staticmethod
    def _load_manifest(dest: Path) -> dict:
        try:
            return json.loads((dest / MANIFEST).read_text(encoding="utf-8")).get("pages", {})
        except (OSError, ValueError, AttributeError):
            return {}

    @staticmethod
    def _cleanup(dest: Path, planches: list[Planche], width: int, manifest: dict) -> None:
        """Supprime fichiers partiels et planches obsolètes (sinon l'ordre serait faussé)."""
        wanted = {f"{p.index:0{width}d}": p.url for p in planches}
        for f in dest.iterdir():
            if f.name.endswith(".part"):
                f.unlink()
            elif f.is_file() and IMAGE_FILE.match(f.name):
                stem = f.name.split(".")[0]
                entry = manifest.get(stem)
                if stem not in wanted or not entry or entry.get("url") != wanted[stem] \
                        or entry.get("file") != f.name:
                    f.unlink()
                    manifest.pop(stem, None)


class _Retryable(Exception):
    pass
