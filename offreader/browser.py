"""Pilotage de Chromium via Playwright (session, cookies, en-têtes)."""

from __future__ import annotations

import asyncio
import base64
import logging

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from .config import Config
from .errors import BrowserError

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


class BrowserSession:
    """Contexte asynchrone : lance Chromium, ouvre l'URL, ferme tout à la sortie."""

    def __init__(self, config: Config):
        self.config = config
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        kwargs = {"headless": self.config.headless}
        if self.config.browser_executable:
            kwargs["executable_path"] = self.config.browser_executable
        try:
            self.browser = await self._pw.chromium.launch(**kwargs)
        except PlaywrightError as exc:
            await self._pw.stop()
            msg = str(exc).splitlines()[0]
            raise BrowserError(
                "Impossible de lancer Chromium. Installez-le avec « playwright install chromium » "
                f"ou indiquez son chemin via la variable OFFREADER_CHROMIUM. Détail : {msg}"
            ) from exc
        w, h = self.config.viewport
        self.context = await self.browser.new_context(viewport={"width": w, "height": h})
        self.context.set_default_timeout(self.config.step_timeout * 1000)
        self.context.set_default_navigation_timeout(self.config.navigation_timeout * 1000)
        self.page = await self.context.new_page()
        # Les fenêtres surgissantes (publicités) sont fermées pour rester sur le lecteur.
        self.context.on("page", self._close_popup)
        return self

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
