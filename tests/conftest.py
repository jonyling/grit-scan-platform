"""Tests never read a real reader's notes or profile. Every test gets an empty folder in place of `secrets/readers`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

if os.environ.get("GRIT_TEST_ROOT"):
    from grit import settings

    root = Path(os.environ["GRIT_TEST_ROOT"]).resolve()
    settings.CONFIG_DIR = root / "config"
    settings.OUTPUT_DIR = root / "output"
    settings.SECRETS_DIR = root / "secrets"
    settings.PROFILES_DIR = settings.CONFIG_DIR / "profiles"
    settings.DEFAULT_DB = settings.OUTPUT_DIR / "grit.sqlite"
    for folder in (settings.CONFIG_DIR, settings.OUTPUT_DIR, settings.SECRETS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    settings.load_local_env = lambda path=None: None


@pytest.fixture(autouse=True)
def _no_real_readers(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIT_READERS_DIR", str(tmp_path_factory.mktemp("readers")))
