"""Vector math in plain Python, so the same vectors give the same numbers on any machine."""

from __future__ import annotations

import math
import operator
import struct
from typing import Sequence

_sumprod = getattr(math, "sumprod", None)


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if norm == 0.0:
        raise ValueError("A zero vector has no direction")
    return [value / norm for value in vector]


def to_float32(vector: Sequence[float]) -> list[float]:
    """Round to float32. Vectors are stored this way, so a cached vector equals a fresh one."""
    return list(struct.unpack(f"<{len(vector)}f", struct.pack(f"<{len(vector)}f", *vector)))


def dot(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second):
        raise ValueError(f"Vectors differ in length: {len(first)} and {len(second)}")
    return _sumprod(first, second) if _sumprod else math.fsum(map(operator.mul, first, second))


def top_matches(vector: Sequence[float], anchors: Sequence[Sequence[float]], k: int = 3) -> list[tuple[int, float]]:
    """The k best anchors as (index, cosine) for unit vectors. A tie goes to the lower index."""
    scores = [dot(vector, anchor) for anchor in anchors]
    order = sorted(range(len(scores)), key=lambda index: (-scores[index], index))[:k]
    return [(index, scores[index]) for index in order]
