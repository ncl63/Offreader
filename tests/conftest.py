import asyncio
import hashlib

import pytest

from offreader.config import Config
from offreader.engine import run

from .server import IMAGES, TestServer


@pytest.fixture(scope="session")
def server():
    with TestServer() as s:
        yield s


@pytest.fixture(autouse=True)
def _reset(server):
    server.reset()
    yield


def fast_config(tmp_path, **overrides) -> Config:
    cfg = Config(output_dir=tmp_path, navigation_timeout=10, step_timeout=5, change_timeout=1.5,
                 scroll_delay=0.15, retry_base_delay=0.05, download_timeout=5, extraction_timeout=90)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def runner(server, tmp_path):
    """Lance le moteur complet sur une page du serveur de test."""
    def _run(path: str, **overrides):
        events = []
        cfg = fast_config(tmp_path, **overrides)
        result = asyncio.run(run(server.url(path), cfg, events.append))
        return result, events
    return _run


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_hashes(names: list[str]) -> list[str]:
    return [sha(IMAGES[n]) for n in names]
