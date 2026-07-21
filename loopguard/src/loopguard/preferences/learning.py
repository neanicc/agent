from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from .models import PreferenceRule
from .store import (
    PreferenceActor,
    PreferenceLearningError,
    PreferenceRecord,
    PreferenceScope,
    PreferenceStore,
)


class PreferenceLearner:
    def __init__(
        self,
        store: PreferenceStore,
        *,
        scope: PreferenceScope,
        actor: PreferenceActor,
        minimum_examples: int = 3,
        minimum_confidence: float = 0.8,
    ) -> None:
        if isinstance(minimum_examples, bool) or not 1 <= minimum_examples <= 10_000:
            raise ValueError("minimum examples must be between 1 and 10000")
        if not 0.5 <= minimum_confidence <= 1:
            raise ValueError("minimum confidence must be between 0.5 and 1")
        self.store = store
        self.scope = scope
        self.actor = actor
        self.minimum_examples = minimum_examples
        self.minimum_confidence = minimum_confidence

    @classmethod
    def for_path(
        cls,
        path: str | Path,
        *,
        scope: PreferenceScope | None = None,
        actor: PreferenceActor | None = None,
        minimum_examples: int = 3,
        minimum_confidence: float = 0.8,
    ) -> PreferenceLearner:
        return cls(
            PreferenceStore(path),
            scope=scope or PreferenceScope.local(),
            actor=actor or PreferenceActor.local(),
            minimum_examples=minimum_examples,
            minimum_confidence=minimum_confidence,
        )

    def record(
        self,
        decision_id: str,
        *,
        choice: str,
        context: dict,
        artifact_hash: str | None = None,
        actor: PreferenceActor | None = None,
    ) -> PreferenceRecord:
        return self.store.record(
            self.scope,
            actor or self.actor,
            decision_id,
            choice,
            context,
            artifact_hash,
        )

    def decision(self, decision_id: str) -> PreferenceRecord:
        result = self.store.decision(self.scope, decision_id)
        if result is None:
            raise PreferenceLearningError("decision does not exist")
        return result

    def derive_rules(self) -> list[PreferenceRule]:
        candidates = self._candidates()
        promotions = self.store.promotions(self.scope)
        rules: list[PreferenceRule] = []
        for candidate in candidates:
            severity = promotions.get(candidate.rule_id, "inform")
            source = "explicit" if candidate.rule_id in promotions else "learned"
            rules.append(
                PreferenceRule(
                    id=candidate.rule_id,
                    source=source,
                    severity=severity,
                    statement=_statement(candidate.choice, candidate.context),
                    parameters={
                        "choice": candidate.choice,
                        "context": candidate.context,
                        "example_count": candidate.example_count,
                        "total_examples": candidate.total_examples,
                        "confidence": candidate.confidence,
                    },
                )
            )
        return sorted(rules, key=lambda rule: rule.id)

    def revoke(
        self,
        decision_id: str,
        *,
        reason: str,
        actor: PreferenceActor | None = None,
    ) -> None:
        self.store.revoke(self.scope, actor or self.actor, decision_id, reason)

    def promote(
        self,
        rule_id: str,
        severity: Literal["warn", "block"],
        *,
        reason: str,
        actor: PreferenceActor | None = None,
    ) -> None:
        if rule_id not in {candidate.rule_id for candidate in self._candidates()}:
            raise PreferenceLearningError("only a current learned rule can be promoted")
        self.store.promote(self.scope, actor or self.actor, rule_id, severity, reason)

    def delete_rule(
        self,
        rule_id: str,
        *,
        reason: str,
        actor: PreferenceActor | None = None,
    ) -> None:
        candidate = next(
            (candidate for candidate in self._candidates() if candidate.rule_id == rule_id), None
        )
        if candidate is None:
            raise PreferenceLearningError("learned rule does not exist")
        self.store.delete_candidate(
            self.scope,
            actor or self.actor,
            rule_id=rule_id,
            context_hash=candidate.context_hash,
            choice=candidate.choice,
            reason=reason,
        )

    def negative_example_count(self, rule_id: str) -> int:
        return self.store.negative_count(self.scope, rule_id)

    def close(self) -> None:
        self.store.close()

    def _candidates(self) -> list[_Candidate]:
        grouped: dict[str, list[PreferenceRecord]] = {}
        for record in self.store.decisions(self.scope):
            grouped.setdefault(record.context_hash, []).append(record)
        candidates: list[_Candidate] = []
        negative_counts = self.store.negative_counts(self.scope)
        for context_hash, records in sorted(grouped.items()):
            counts = Counter(record.choice for record in records)
            choice, example_count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
            negative_count = negative_counts.get(
                (context_hash, hashlib.sha256(choice.encode()).hexdigest()), 0
            )
            total_examples = len(records) + negative_count
            confidence = example_count / total_examples
            if example_count < self.minimum_examples or confidence < self.minimum_confidence:
                continue
            context = records[0].context
            candidates.append(
                _Candidate(
                    rule_id=_rule_id(choice, context_hash),
                    choice=choice,
                    context=context,
                    context_hash=context_hash,
                    example_count=example_count,
                    total_examples=total_examples,
                    confidence=confidence,
                )
            )
        return candidates


class _Candidate:
    def __init__(
        self,
        *,
        rule_id: str,
        choice: str,
        context: dict,
        context_hash: str,
        example_count: int,
        total_examples: int,
        confidence: float,
    ) -> None:
        self.rule_id = rule_id
        self.choice = choice
        self.context = context
        self.context_hash = context_hash
        self.example_count = example_count
        self.total_examples = total_examples
        self.confidence = confidence


def _rule_id(choice: str, context_hash: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", choice.casefold()).strip("-")[:32] or "preference"
    digest = hashlib.sha256(f"{context_hash}\0{choice}".encode()).hexdigest()[:16]
    return f"learned-{slug}-{digest}"


def _statement(choice: str, context: dict) -> str:
    rendered = [f"{key}={str(value)[:80]}" for key, value in sorted(context.items())[:8]]
    if len(context) > 8:
        rendered.append(f"and {len(context) - 8} more dimensions")
    dimensions = ", ".join(rendered)
    return f"Prefer {choice} when {dimensions}." if dimensions else f"Prefer {choice}."
