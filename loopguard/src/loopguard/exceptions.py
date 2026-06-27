from __future__ import annotations

from .decision import LoopDecision


class LoopDetectedError(RuntimeError):
    def __init__(self, decision: LoopDecision):
        super().__init__(decision.reason)
        self.decision = decision
