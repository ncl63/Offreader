"""Exécution du moteur en arrière-plan pour une interface graphique.

Le moteur est asynchrone ; une interface (Tk, Qt…) a sa propre boucle
d'événements bloquante. `DownloadJob` exécute donc `engine.run` dans un thread
dédié, avec sa propre boucle asyncio, et transmet tout ce qui se passe sous
forme de messages dans une `queue.Queue` que l'interface lit sans jamais
attendre. Aucun code d'interface ici : ce module se teste sans affichage.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .engine import run
from .errors import IncompleteChapter, OffreaderError
from .models import ProgressEvent

log = logging.getLogger(__name__)


@dataclass
class JobMessage:
    """Message du moteur vers l'interface.

    kind :
      - "progress"  : `event` contient un ProgressEvent
      - "done"      : `output` est le CBZ ou le dossier créé, `count` le nombre de planches
      - "incomplete": chapitre incomplet (`text`), planches conservées dans `output`
      - "error"     : échec (`text` lisible par l'utilisateur)
      - "cancelled" : arrêt demandé par l'utilisateur
    """

    kind: str
    event: ProgressEvent | None = None
    text: str = ""
    output: Path | None = None
    count: int = 0


class DownloadJob:
    def __init__(self, url: str, config: Config):
        self.url = url
        self.config = config
        self.messages: queue.Queue[JobMessage] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._cancel_requested = threading.Event()
        self._thread = threading.Thread(target=self._main, name="offreader-job", daemon=True)

    # --------------------------------------------------------------- interface

    def start(self) -> "DownloadJob":
        self._thread.start()
        return self

    def cancel(self) -> None:
        """Demande l'arrêt ; le navigateur est fermé proprement, la reprise reste possible."""
        self._cancel_requested.set()
        loop, task = self._loop, self._task
        if loop and task and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def poll(self) -> list[JobMessage]:
        """Messages en attente, sans bloquer (à appeler depuis le thread de l'interface)."""
        out = []
        while True:
            try:
                out.append(self.messages.get_nowait())
            except queue.Empty:
                return out

    # ----------------------------------------------------------------- thread

    def _emit(self, event: ProgressEvent) -> None:
        self.messages.put(JobMessage("progress", event=event))

    def _main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    async def _run(self) -> None:
        self._task = asyncio.current_task()
        if self._cancel_requested.is_set():
            self.messages.put(JobMessage("cancelled", text="Téléchargement annulé."))
            return
        try:
            result = await run(self.url, self.config, self._emit)
        except asyncio.CancelledError:
            self.messages.put(JobMessage("cancelled", text="Téléchargement annulé. Relancez pour reprendre."))
        except IncompleteChapter as exc:
            self.messages.put(JobMessage("incomplete", text=str(exc), output=Path(self.config.output_dir)))
        except OffreaderError as exc:
            self.messages.put(JobMessage("error", text=str(exc)))
        except Exception as exc:  # erreur imprévue : jamais de thread qui meurt en silence
            log.exception("Erreur inattendue")
            self.messages.put(JobMessage("error", text=f"Erreur inattendue : {type(exc).__name__}: {exc}"))
        else:
            self.messages.put(JobMessage("done", output=result.output, count=len(result.extraction.planches)))
