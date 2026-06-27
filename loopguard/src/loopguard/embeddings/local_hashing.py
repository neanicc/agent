from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

from .base import Embedder, cosine_similarity


class LocalHashingEmbedder(Embedder):
    def __init__(self, n_features: int = 1024):
        self.vectorizer = HashingVectorizer(
            n_features=n_features,
            alternate_sign=False,
            norm="l2",
            analyzer="char_wb",
            ngram_range=(3, 5),
            lowercase=True,
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.vectorizer.transform(texts).toarray().astype(float)


__all__ = ["LocalHashingEmbedder", "cosine_similarity"]
