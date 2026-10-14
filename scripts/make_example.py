"""Génère examples/exemple_vertical.cbz à partir du lecteur de test local.

    python scripts/make_example.py
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from offreader.config import Config  # noqa: E402
from offreader.engine import run  # noqa: E402
from tests.server import TestServer  # noqa: E402


def main() -> None:
    with TestServer() as server, tempfile.TemporaryDirectory() as tmp:
        cfg = Config(output_dir=Path(tmp), name="exemple_vertical")
        result = asyncio.run(run(server.url("/sites/parasites.html"), cfg, lambda e: print(e.kind, e.message, e.current or "")))
        target = ROOT / "examples" / "exemple_vertical.cbz"
        target.parent.mkdir(exist_ok=True)
        shutil.copy(result.output, target)
        print("Exemple créé :", target)


if __name__ == "__main__":
    main()
