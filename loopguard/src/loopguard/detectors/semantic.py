from __future__ import annotations

from itertools import combinations

from loopguard.config import LoopGuardConfig
from loopguard.decision import LoopDecision
from loopguard.embeddings.base import cosine_similarity
from loopguard.embeddings.local_hashing import LocalHashingEmbedder
from loopguard.event import LoopEvent
from loopguard.normalize import normalize_event


def detect(
    events: list[LoopEvent], config: LoopGuardConfig, embedder: LocalHashingEmbedder | None = None
) -> LoopDecision:
    n = config.trip_count
    if len(events) < n:
        return LoopDecision()
    tail = events[-n:]
    texts = [normalize_event(e, config) for e in tail]
    vectors = (embedder or LocalHashingEmbedder()).embed(texts)
    sims = [cosine_similarity(vectors[i], vectors[j]) for i, j in combinations(range(n), 2)]
    min_sim = min(sims) if sims else 0.0
    if min_sim >= config.semantic_threshold:
        return LoopDecision(
            allowed=False,
            tripped=True,
            reason=f"Semantic loop detected across last {n} events",
            detector="semantic",
            similarity=min_sim,
            matching_events=tail,
        )
    return LoopDecision()
