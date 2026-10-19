"""Tests never read a real reader's notes or profile: secrets/readers is replaced by an empty folder for every test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_readers(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIT_READERS_DIR", str(tmp_path_factory.mktemp("readers")))
