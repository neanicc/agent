from __future__ import annotations

from loopguard.config import LoopGuardConfig
from loopguard.decision import LoopDecision
from loopguard.embeddings.base import cosine_similarity
from loopguard.embeddings.local_hashing import LocalHashingEmbedder
from loopguard.event import LoopEvent
from loopguard.normalize import exact_signature, normalize_event


def detect(
    events: list[LoopEvent], config: LoopGuardConfig, embedder: LocalHashingEmbedder | None = None
) -> LoopDecision:
    if len(events) < 4:
        return LoopDecision()
    tail = events[-4:]
    sigs = [exact_signature(e, config) for e in tail]
    if sigs[0] == sigs[2] and sigs[1] == sigs[3] and sigs[0] != sigs[1]:
        return LoopDecision(
            allowed=False,
            tripped=True,
            reason="Ping-pong A-B-A-B loop detected",
            detector="pingpong",
            similarity=1.0,
            matching_events=tail,
        )
    texts = [normalize_event(e, config) for e in tail]
    vecs = (embedder or LocalHashingEmbedder()).embed(texts)
    s02 = cosine_similarity(vecs[0], vecs[2])
    s13 = cosine_similarity(vecs[1], vecs[3])
    if min(s02, s13) >= config.semantic_threshold:
        return LoopDecision(
            allowed=False,
            tripped=True,
            reason="Semantic ping-pong A-B-A-B loop detected",
            detector="pingpong",
            similarity=min(s02, s13),
            matching_events=tail,
        )
    return LoopDecision()
