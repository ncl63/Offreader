"""Pilotage de Chromium via Playwright (session, cookies, en-têtes)."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from .config import Config
from .errors import BrowserError
from .models import ProgressEvent

log = logging.getLogger(__name__)

_FETCH_BLOB_JS = """
async (url) => {
  const r = await fetch(url);
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(s);
}
"""


def use_bundled_browsers() -> None:
    """Exécutable autonome : Chromium est embarqué dans le paquet Playwright.

    Playwright ne l'y cherche que si PLAYWRIGHT_BROWSERS_PATH vaut « 0 » au
    lancement ; sinon il chercherait dans %LOCALAPPDATA%\\ms-playwright.
    """
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        return
    import playwright

    bundled = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    if bundled.is_dir() and any(bundled.iterdir()):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"


class BrowserSession:
    """Contexte asynchrone : lance Chromium, ouvre l'URL, ferme tout à la sortie."""

    def __init__(self, config: Config, on_event: Callable[[ProgressEvent], None] | None = None):
        self.config = config
        self.on_event = on_event or (lambda e: None)
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        try:
            self.browser = await self._launch()
        except BaseException:
            await self._pw.stop()
            raise
        try:
            w, h = self.config.viewport
            self.context = await self.browser.new_context(viewport={"width": w, "height": h})
            self.context.set_default_timeout(self.config.step_timeout * 1000)
            self.context.set_default_navigation_timeout(self.config.navigation_timeout * 1000)
            self.page = await self.context.new_page()
            # Les fenêtres surgissantes (publicités) sont fermées pour rester sur le lecteur.
            self.context.on("page", self._close_popup)
        except BaseException:  # y compris une annulation : ne jamais laisser Chromium ouvert
            await self.__aexit__(None, None, None)
            raise
        return self

    async def _launch(self) -> Browser:
        """Lance un navigateur Chromium, avec des solutions de repli.

        1. le chemin configuré (OFFREADER_CHROMIUM), s'il est fourni ;
        2. le Chromium de Playwright (installé ou embarqué dans l'exécutable) ;
        3. sous Windows : Microsoft Edge (présent sur Windows 11), puis Google Chrome ;
        4. en dernier recours, téléchargement automatique de Chromium, puis nouvel essai.
        """
        base = {"headless": self.config.headless}
        if sys.platform.startswith("linux"):  # conteneurs Docker : /dev/shm souvent trop petit
            base["args"] = ["--disable-dev-shm-usage"]
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:  # proxy explicite (réseau d'entreprise) ; sinon réglages système
            no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            base["proxy"] = {"server": proxy, "bypass": no_proxy}

        if self.config.browser_executable:
            attempts = [("navigateur configuré", {"executable_path": self.config.browser_executable})]
        else:
            attempts = [("Chromium", {})]
            if sys.platform == "win32":
                attempts += [("Microsoft Edge", {"channel": "msedge"}), ("Google Chrome", {"channel": "chrome"})]

        errors = []
        for label, extra in attempts:
            try:
                browser = await self._pw.chromium.launch(**base, **extra)
                log.debug("Navigateur utilisé : %s", label)
                return browser
            except PlaywrightError as exc:
                errors.append(f"{label} : {str(exc).splitlines()[0]}")

        if not self.config.browser_executable and self.config.auto_install_browser:
            self.on_event(ProgressEvent("analysis", "Installation de Chromium (première utilisation)..."))
            install_error = await asyncio.to_thread(install_chromium, self.config.headless)
            if install_error is None:
                try:
                    return await self._pw.chromium.launch(**base)
                except PlaywrightError as exc:
                    errors.append(f"Chromium après installation : {str(exc).splitlines()[0]}")
            else:
                errors.append(install_error)

        raise BrowserError(
            "Impossible de lancer un navigateur. Installez Chromium avec « playwright install chromium », "
            "ou indiquez le chemin de chrome.exe / msedge.exe dans la variable OFFREADER_CHROMIUM.\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    async def __aexit__(self, *exc) -> None:
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    def _close_popup(self, page: Page) -> None:
        if page is not self.page:
            asyncio.ensure_future(_safe_close(page))

    async def open(self, url: str) -> None:
        log.debug("Ouverture de %s", url)
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise BrowserError(f"Impossible d'ouvrir la page {url} : {str(exc).splitlines()[0]}") from exc
        if response is not None and response.status >= 400:
            raise BrowserError(f"La page {url} a répondu avec l'erreur HTTP {response.status}.")
        await self.settle()

    async def settle(self, timeout: float | None = None) -> None:
        """Attend (de façon bornée) la fin du chargement de la page."""
        timeout = self.config.navigation_timeout if timeout is None else timeout
        for state in ("load", "networkidle"):
            try:
                await self.page.wait_for_load_state(state, timeout=timeout * 1000)
            except PlaywrightError:
                log.debug("État %s non atteint après %.1fs, on continue", state, timeout)
            timeout = min(timeout, 3.0)  # networkidle est un bonus, pas une condition

    async def http_identity(self) -> tuple[list[dict], dict[str, str]]:
        """Cookies et en-têtes du navigateur à réutiliser pour les téléchargements."""
        cookies = await self.context.cookies()
        user_agent = await self.page.evaluate("navigator.userAgent")
        headers = {
            "User-Agent": user_agent,
            "Referer": self.page.url,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        return cookies, headers

    async def fetch_blob(self, url: str) -> bytes:
        """Récupère le contenu d'une URL blob:, qui n'existe que dans la page."""
        return base64.b64decode(await self.page.evaluate(_FETCH_BLOB_JS, url))


async def _safe_close(page: Page) -> None:
    try:
        await page.close()
    except PlaywrightError:
        pass


def install_chromium(only_shell: bool = False, timeout: float = 900) -> str | None:
    """Télécharge le Chromium de Playwright. Renvoie None si tout va bien, sinon l'erreur.

    Passe par le pilote de Playwright plutôt que par « python -m playwright » :
    cela fonctionne aussi depuis l'exécutable PyInstaller.
    """
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    driver = compute_driver_executable()
    cmd = [*driver] if isinstance(driver, (tuple, list)) else [str(driver)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # pas de console qui clignote sous Windows
    try:
        args = ["install", "chromium", *(["--only-shell"] if only_shell else [])]
        proc = subprocess.run([*cmd, *args], env=get_driver_env(), capture_output=True,
                              text=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"installation de Chromium impossible : {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["erreur inconnue"]
        return f"installation de Chromium impossible : {tail[0]}"
    return None
