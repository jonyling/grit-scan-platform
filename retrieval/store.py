"""A rebuildable cache of vectors. Dataverse stays the system of record. Delete the file and it refills.

A vector is keyed by model, task, dimensions, and the hash of the text. An unchanged text is never embedded twice.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
from pathlib import Path
from typing import Protocol

_CHUNK = 400


class _Embeds(Protocol):
    model: str
    dimensions: int
    batch_size: int

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]: ...


def text_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


class VectorStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vectors (model TEXT, task TEXT, dimensions INTEGER, text_hash TEXT, vector BLOB,"
            " PRIMARY KEY (model, task, dimensions, text_hash))"
        )
        self.hits = 0
        self.embedded = 0

    def close(self) -> None:
        self._db.close()

    def vectors(self, texts: list[str], *, task: str, embedder: _Embeds) -> list[list[float]]:
        """One vector per text, in order. Cached vectors are read, and missing ones are embedded in batches and saved."""
        keys = [text_hash(text) for text in texts]
        found = self._read(sorted(set(keys)), task, embedder)
        missing = [key for key in dict.fromkeys(keys) if key not in found]
        self.hits += len(set(keys)) - len(missing)
        by_key = dict(zip(keys, texts))
        for start in range(0, len(missing), embedder.batch_size):
            batch = missing[start : start + embedder.batch_size]
            for key, vector in zip(batch, embedder.embed([by_key[key] for key in batch], task=task)):
                found[key] = vector
                self._db.execute(
                    "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?)",
                    (embedder.model, task, embedder.dimensions, key, struct.pack(f"<{len(vector)}f", *vector)),
                )
            self._db.commit()
            self.embedded += len(batch)
        return [found[key] for key in keys]

    def _read(self, keys: list[str], task: str, embedder: _Embeds) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        for start in range(0, len(keys), _CHUNK):
            chunk = keys[start : start + _CHUNK]
            rows = self._db.execute(
                f"SELECT text_hash, vector FROM vectors WHERE model = ? AND task = ? AND dimensions = ? AND text_hash IN ({','.join('?' * len(chunk))})",
                (embedder.model, task, embedder.dimensions, *chunk),
            ).fetchall()
            for key, blob in rows:
                found[key] = list(struct.unpack(f"<{len(blob) // 4}f", blob))
        return found
