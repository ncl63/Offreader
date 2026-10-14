"""Serveur HTTP local pour les tests : images générées, lecteurs simulés, pannes réseau.

Les pannes (/flaky, /trunc, /corrupt) ne concernent que les téléchargements
hors navigateur (requêtes sans en-tête Sec-Fetch-Dest) : le navigateur affiche
la page normalement, puis le gestionnaire de téléchargement subit les erreurs.
"""

from __future__ import annotations

import io
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw

SITES = Path(__file__).parent / "sites"
MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
PIL_FMT = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF"}

# Planches : p01..p12, formats variés.
PAGE_FORMATS = ["jpg", "webp", "png", "jpg", "jpg", "webp", "png", "jpg", "jpg", "webp", "jpg", "png"]
PAGES = [f"p{n:02d}.{fmt}" for n, fmt in enumerate(PAGE_FORMATS, 1)]
# Noms non séquentiels pour les lecteurs page par page.
HASHED = ["k7f2.jpg", "a91c.jpg", "zz03.jpg", "m4q8.jpg", "b0d5.jpg", "x6y1.jpg"]


def make_image(text: str, size: tuple[int, int], color: tuple[int, int, int], fmt: str) -> bytes:
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    w, h = size
    if w > 40 and h > 40:
        draw.rectangle([10, 10, w - 10, h - 10], outline=(0, 0, 0), width=4)
    draw.text((w // 2 - 20, h // 2), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, PIL_FMT[fmt], quality=85)
    return buf.getvalue()


def build_images() -> dict[str, bytes]:
    images: dict[str, bytes] = {}
    for n, name in enumerate(PAGES, 1):
        images[name] = make_image(f"PAGE {n}", (400, 600), ((n * 53) % 200 + 40, (n * 97) % 200 + 40, (n * 31) % 200 + 40), name.split(".")[1])
    for n, name in enumerate(HASHED, 1):
        images[name] = make_image(f"HASH {n}", (500, 720), ((n * 71) % 200 + 40, 90, (n * 43) % 200 + 40), "jpg")
    blank = make_image("", (400, 600), (255, 255, 255), "jpg")
    images["blank-a.jpg"] = blank
    images["blank-b.jpg"] = blank  # contenu identique, URL différente : deux planches distinctes
    images["logo.png"] = make_image("LOGO", (180, 50), (200, 0, 0), "png")
    images["banner.jpg"] = make_image("PUB", (728, 90), (255, 200, 0), "jpg")
    images["inline-ad.jpg"] = make_image("AD", (400, 300), (255, 100, 0), "jpg")
    images["avatar.png"] = make_image("A", (64, 64), (0, 0, 200), "png")
    images["pixel.gif"] = make_image("", (1, 1), (255, 255, 255), "gif")
    for n in range(1, 9):
        images[f"thumb{n}.jpg"] = make_image(f"T{n}", (200, 300), (120, 120, 120 + n * 10), "jpg")
    return images


IMAGES = build_images()


def generated(k: int) -> bytes:
    return make_image(f"GEN {k}", (300, 450), ((k * 37) % 255, (k * 11) % 255, 128), "png")


PAGED_CHAPTER_LEN = 5
QUERY_LEN = 4


def paged_html(chap: int, n: int, trap: bool) -> str:
    """Lecteur à pages séparées (/paged/<chap>/<n>), navigation par lien."""
    img = PAGES[(n - 1 + (chap - 1) * 5) % len(PAGES)]
    nav = []
    if n > 1:
        nav.append(f'<a class="nav" href="/paged{"-trap" if trap else ""}/{chap}/{n - 1}">‹</a>')
    if n < PAGED_CHAPTER_LEN:
        nav.append(f'<a class="nav" href="/paged{"-trap" if trap else ""}/{chap}/{n + 1}">›</a>')
    elif trap:
        # Piège : la flèche de la dernière page mène au chapitre suivant, sans le dire.
        nav.append(f'<a class="nav" href="/paged-trap/{chap + 1}/1">›</a>')
    else:
        nav.append(f'<a class="nav" href="/paged/{chap + 1}/1">Chapitre suivant</a>')
    counter = f"<span class='counter'>Page {n} of {PAGED_CHAPTER_LEN}</span>" if not trap else ""
    return f"""<!doctype html><html><head><title>Chapitre {chap} - Lien</title></head><body>
<header><img src="/img/logo.png" class="site-logo"></header>
<div class="viewer"><img class="page-img" src="/img/{img}"></div>
<div class="controls">{''.join(nav)} {counter}</div>
</body></html>"""


def query_html(n: int) -> str:
    """Lecteur ?page=N sans bouton : seule l'URL permet d'avancer ; 404 après la fin."""
    return f"""<!doctype html><html><head><title>Lecteur URL</title></head><body>
<main class="reader"><img src="/img/{HASHED[n - 1]}" width="500" height="720"></main>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silencieux
        pass

    @property
    def state(self):
        return self.server.state

    def _send(self, status: int, body: bytes, ctype: str, length: int | None = None, close=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        self.send_header("Cache-Control", "no-store")
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        if close:
            self.close_connection = True

    def do_GET(self):
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        browser = "Sec-Fetch-Dest" in self.headers
        with self.server.lock:
            self.state["hits"][path] += 1
            if not browser:
                self.state["downloads"][path] += 1
            count = self.state["downloads"].get(path, 0)

        if path.startswith("/sites/"):
            f = SITES / path.removeprefix("/sites/")
            if f.is_file():
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            return self._send(404, b"not found", "text/plain")

        segs = path.strip("/").split("/")
        kind, name = segs[0], segs[-1]
        if kind in ("img", "flaky", "trunc", "corrupt", "gone") and len(segs) == 2:
            data = IMAGES.get(name)
            if data is None:
                return self._send(404, b"not found", "text/plain")
            ctype = MIME[name.rsplit(".", 1)[1]]
            if "delay" in query:
                time.sleep(int(query["delay"][0]) / 1000)
            if kind == "gone" and not self.state["gone_fixed"]:
                return self._send(404, b"gone", "text/plain")
            if not browser:
                if kind == "flaky" and count <= int(query.get("fail", ["2"])[0]):
                    return self._send(503, b"busy", "text/plain")
                if kind == "trunc" and count == 1:
                    # Annonce la taille complète mais coupe la connexion à mi-chemin.
                    return self._send(200, data[: len(data) // 2], ctype, length=len(data), close=True)
                if kind == "corrupt" and count == 1:
                    # Réponse « valide » au niveau HTTP mais image tronquée.
                    return self._send(200, data[: len(data) // 2], ctype)
            return self._send(200, data, ctype)

        if kind == "gen" and len(segs) == 2:
            return self._send(200, generated(int(name.split(".")[0])), "image/png")

        if kind in ("paged", "paged-trap") and len(segs) == 3:
            chap, n = int(segs[1]), int(segs[2])
            if 1 <= n <= PAGED_CHAPTER_LEN:
                return self._send(200, paged_html(chap, n, kind == "paged-trap").encode(), "text/html; charset=utf-8")
            return self._send(404, b"not found", "text/plain")

        if kind == "query":
            n = int(query.get("page", ["1"])[0])
            if 1 <= n <= QUERY_LEN:
                return self._send(200, query_html(n).encode(), "text/html; charset=utf-8")
            return self._send(404, b"<h1>Page introuvable</h1>", "text/html")

        return self._send(404, b"not found", "text/plain")


class TestServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.lock = threading.Lock()
        self.reset()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def state(self):
        return self.httpd.state

    def reset(self):
        self.httpd.state = {"hits": defaultdict(int), "downloads": defaultdict(int), "gone_fixed": False}

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def url(self, path: str) -> str:
        return self.base + path

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


if __name__ == "__main__":  # serveur manuel : python -m tests.server
    with TestServer() as s:
        print("Serveur de test :", s.url("/sites/vertical.html"))
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
