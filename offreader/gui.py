"""Application graphique Windows (CustomTkinter).

L'interface ne contient aucune logique d'extraction : elle crée un
`DownloadJob` (moteur exécuté dans un thread) et lit ses messages toutes les
100 ms. La fenêtre reste donc réactive pendant toute l'opération.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from . import __version__
from .browser import use_bundled_browsers
from .config import Config
from .jobs import DownloadJob, JobMessage

POLL_MS = 100
ERROR_COLOR = ("#b3261e", "#f2b8b5")
MUTED_COLOR = ("gray35", "gray65")


def log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
    return Path(base) / "Offreader" / "offreader.log"


def setup_logging() -> None:
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def open_in_explorer(path: Path) -> None:
    """Ouvre le dossier ; sélectionne le fichier s'il s'agit du CBZ."""
    if sys.platform == "win32":
        if path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        folder = path if path.is_dir() else path.parent
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


class App(ctk.CTk):
    def __init__(self, config_factory: Callable[[], Config] = Config,
                 opener: Callable[[Path], None] = open_in_explorer):
        super().__init__()
        self.config_factory = config_factory
        self.opener = opener
        self.job: DownloadJob | None = None
        self.output: Path | None = None
        self._closing = False

        self.title("Offreader")
        self.geometry("620x250")
        self.minsize(480, 250)
        self.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(self, placeholder_text="URL du chapitre", height=36)
        self.url_entry.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(16, 8))
        self.url_entry.bind("<Return>", lambda e: self.on_main_button())

        self.format_var = ctk.StringVar(value="CBZ")
        self.format_choice = ctk.CTkSegmentedButton(self, values=["CBZ", "Images"], variable=self.format_var)
        self.format_choice.grid(row=1, column=0, sticky="w", padx=16)

        self.main_button = ctk.CTkButton(self, text="Télécharger", width=140, height=34,
                                         command=self.on_main_button)
        self.main_button.grid(row=1, column=2, sticky="e", padx=16)

        self.progress = ctk.CTkProgressBar(self, height=10)
        self.progress.grid(row=2, column=0, columnspan=3, sticky="ew", padx=16, pady=(16, 6))
        self.progress.set(0)

        self.pages_label = ctk.CTkLabel(self, text="", anchor="w")
        self.pages_label.grid(row=3, column=0, sticky="w", padx=16)
        self.status_label = ctk.CTkLabel(self, text="", anchor="e", text_color=MUTED_COLOR)
        self.status_label.grid(row=3, column=1, columnspan=2, sticky="e", padx=16)

        self.error_label = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=580,
                                        text_color=ERROR_COLOR, font=ctk.CTkFont(size=12))
        self.error_label.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16)

        self.open_button = ctk.CTkButton(self, text="Ouvrir le dossier", width=140, height=30,
                                         command=self.on_open, state="disabled",
                                         fg_color="transparent", border_width=1,
                                         text_color=("gray10", "gray90"))
        self.open_button.grid(row=5, column=2, sticky="e", padx=16, pady=(4, 16))

        self.bind("<Configure>", self._on_resize)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_id = self.after(POLL_MS, self._poll)

    def destroy(self) -> None:
        try:
            self.after_cancel(self._poll_id)
        except Exception:
            pass
        super().destroy()

    # ------------------------------------------------------------- actions

    def on_main_button(self) -> None:
        if self.job and self.job.running:
            self.job.cancel()
            self.main_button.configure(state="disabled", text="Annulation...")
            return
        self.start_download()

    def start_download(self) -> None:
        url = self.url_entry.get().strip()
        if not url.lower().startswith(("http://", "https://")):
            self.show_error("Collez une adresse commençant par http:// ou https://")
            return
        config = self.config_factory()
        config.format = "cbz" if self.format_var.get() == "CBZ" else "images"
        self.output = None
        self.show_error("")
        self.open_button.configure(state="disabled")
        self.pages_label.configure(text="")
        self.status_label.configure(text="Analyse du lecteur...")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.main_button.configure(text="Annuler")
        self.url_entry.configure(state="disabled")
        self.format_choice.configure(state="disabled")
        logging.getLogger(__name__).info("Téléchargement de %s (%s)", url, config.format)
        self.job = DownloadJob(url, config).start()

    def on_open(self) -> None:
        if self.output:
            self.opener(self.output)

    def on_close(self) -> None:
        if self.job and self.job.running:
            self._closing = True
            self.job.cancel()
            self.status_label.configure(text="Fermeture...")
            self.after(POLL_MS, self._close_when_idle, 100)  # 10 s au plus
        else:
            self.destroy()

    def _close_when_idle(self, remaining: int) -> None:
        if self.job and self.job.running and remaining > 0:
            self.after(POLL_MS, self._close_when_idle, remaining - 1)
        else:
            self.destroy()

    # ------------------------------------------------------------- affichage

    def show_error(self, text: str) -> None:
        lines = [l for l in text.splitlines() if l.strip()]
        shown = "\n".join(lines[:4]) + ("\n..." if len(lines) > 4 else "")
        self.error_label.configure(text=shown)

    def _on_resize(self, event) -> None:
        if event.widget is self:
            self.error_label.configure(wraplength=max(300, event.width - 40))

    def _poll(self) -> None:
        if self.job:
            for msg in self.job.poll():
                self.handle(msg)
        if not self._closing:
            self._poll_id = self.after(POLL_MS, self._poll)

    def handle(self, msg: JobMessage) -> None:
        if msg.kind == "progress":
            e = msg.event
            if e.kind == "pages":
                total = f" / {e.total}" if e.total else ""
                self.pages_label.configure(text=f"Pages : {e.current}{total}")
            elif e.kind == "download":
                if self.progress.cget("mode") != "determinate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                self.progress.set(e.current / e.total if e.total else 1)
                self.pages_label.configure(text=f"Pages : {e.current} / {e.total}")
                self.status_label.configure(text="Téléchargement...")
            elif e.kind == "mode":
                self.status_label.configure(text=f"Lecteur : {e.message}")
            elif e.kind == "warning":
                self.show_error(e.message)
            else:
                self.status_label.configure(text=e.message)
            return

        # Fin du travail, quelle qu'en soit l'issue.
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.main_button.configure(state="normal", text="Télécharger")
        self.url_entry.configure(state="normal")
        self.format_choice.configure(state="normal")
        if msg.kind == "done":
            self.output = msg.output
            self.progress.set(1)
            self.pages_label.configure(text=f"Pages : {msg.count} / {msg.count}")
            self.status_label.configure(text="Terminé")
            self.open_button.configure(state="normal")
        elif msg.kind == "incomplete":
            self.output = msg.output
            self.status_label.configure(text="Incomplet")
            self.show_error(msg.text)
            self.open_button.configure(state="normal")
        elif msg.kind == "cancelled":
            self.progress.set(0)
            self.status_label.configure(text="Annulé")
        else:
            self.progress.set(0)
            self.status_label.configure(text="Échec")
            self.show_error(msg.text)


def main() -> None:
    setup_logging()
    use_bundled_browsers()
    logging.getLogger(__name__).info("Offreader %s", __version__)
    ctk.set_appearance_mode("system")
    App().mainloop()


if __name__ == "__main__":
    main()
