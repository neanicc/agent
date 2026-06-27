from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class Embedder(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray: ...


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
