from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .accessibility import axe_findings, axe_rule_ids, ios_missing_accessible_names
from .models import PreferenceProfile, PreferenceRule, PreferenceVerdict


_COLOR_LITERAL = re.compile(
    r"(?<![\w-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})"
    r"(?![0-9a-fA-F\w-])"
)
_MAX_ARTIFACT_PAYLOAD = 1024 * 1024


class PreferenceEvaluationError(ValueError):
    """Artifact evaluation could not preserve its deterministic input contract."""


class PreferenceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(min_length=1, max_length=512)
    kind: Literal[
        "source_diff",
        "web_axe",
        "ios_accessibility",
        "text_metadata",
        "motion_metadata",
        "screenshot_metadata",
    ]
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def bounded_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("preference artifact payload must be finite JSON data") from exc
        if len(encoded) > _MAX_ARTIFACT_PAYLOAD:
            raise ValueError("preference artifact payload exceeds 1 MiB")
        _bounded_tree(value)
        return value

    def with_added(self, text: str) -> PreferenceArtifact:
        if self.kind != "source_diff" or not isinstance(text, str) or not text:
            raise ValueError("added text requires a source-diff artifact")
        existing = self.payload.get("added_text", "")
        if not isinstance(existing, str):
            raise ValueError("source-diff added_text must be a string")
        return PreferenceArtifact.model_validate(
            {
                **self.model_dump(mode="json"),
                "payload": {**self.payload, "added_text": existing + "\n" + text},
            }
        )

    def with_violation(self, rule_id: str) -> PreferenceArtifact:
        if self.kind != "web_axe" or not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("axe violations require a web-axe artifact")
        existing = self.payload.get("violations", [])
        if not isinstance(existing, list):
            raise ValueError("axe violations must be a list")
        return PreferenceArtifact.model_validate(
            {
                **self.model_dump(mode="json"),
                "payload": {**self.payload, "violations": [*existing, {"id": rule_id}]},
            }
        )


class PreferenceEvaluator(Protocol):
    name: str
    version: str

    def supports(self, artifact: PreferenceArtifact) -> bool: ...

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]: ...


class DesignTokenLiteralEvaluator:
    name = "design-token-literals"
    version = "1.0.0"

    def __init__(self, tokens: Mapping[str, Any]) -> None:
        self._allowed = {
            value.casefold()
            for value in tokens.values()
            if isinstance(value, str) and _COLOR_LITERAL.fullmatch(value)
        }

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind == "source_diff"

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        added = artifact.payload.get("added_text")
        if not isinstance(added, str):
            return [_verdict(self, rule, artifact, "inconclusive", "Source diff text is absent.")]
        configured = rule.parameters.get("allowed_literals", [])
        if not isinstance(configured, list) or not all(
            isinstance(item, str) for item in configured
        ):
            return [_verdict(self, rule, artifact, "inconclusive", "Allowed literals are invalid.")]
        allowed = self._allowed.union(item.casefold() for item in configured)
        literals = sorted(set(_COLOR_LITERAL.findall(added)), key=str.casefold)
        if literals and not allowed:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "inconclusive",
                    "No declared color tokens are available for comparison.",
                )
            ]
        unknown = [literal for literal in literals if literal.casefold() not in allowed]
        if unknown:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "violation",
                    f"Unknown color literals were added: {', '.join(unknown[:20])}.",
                )
            ]
        return [_verdict(self, rule, artifact, "pass", "Added colors use declared tokens.")]


class RequiredComponentEvaluator:
    name = "required-component"
    version = "1.0.0"

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind == "source_diff"

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        required = rule.parameters.get("required_imports", [])
        imports = artifact.payload.get("imports", [])
        required_components = rule.parameters.get("required_components", [])
        components = artifact.payload.get("components", [])
        if not all(
            isinstance(items, list) and all(isinstance(item, str) for item in items)
            for items in (required, imports, required_components, components)
        ):
            return [
                _verdict(self, rule, artifact, "inconclusive", "Component/import data is invalid.")
            ]
        if not required and not required_components:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "inconclusive",
                    "No required components or imports are configured.",
                )
            ]
        missing = [
            *sorted(set(required).difference(imports)),
            *sorted(set(required_components).difference(components)),
        ]
        if missing:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "violation",
                    f"Required imports are missing: {', '.join(missing[:20])}.",
                )
            ]
        return [_verdict(self, rule, artifact, "pass", "Required components are imported.")]


class AxeAccessibilityEvaluator:
    name = "axe-accessibility"
    version = "1.0.0"

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind == "web_axe"

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        violations, incomplete, valid = axe_findings(artifact.payload, axe_rule_ids(rule))
        if not valid:
            return [_verdict(self, rule, artifact, "inconclusive", "Axe results are malformed.")]
        if violations:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "violation",
                    f"Axe reported: {', '.join(violations)}.",
                )
            ]
        if incomplete:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "inconclusive",
                    f"Axe could not determine: {', '.join(incomplete)}.",
                )
            ]
        return [_verdict(self, rule, artifact, "pass", "Relevant Axe checks passed.")]


class AccessibleNameEvaluator:
    name = "accessibility-name"
    version = "1.0.0"

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind in {"web_axe", "ios_accessibility"}

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        if artifact.kind == "web_axe":
            violations, incomplete, valid = axe_findings(artifact.payload, axe_rule_ids(rule))
            if not valid:
                return [
                    _verdict(self, rule, artifact, "inconclusive", "Axe results are malformed.")
                ]
            if violations:
                return [
                    _verdict(
                        self,
                        rule,
                        artifact,
                        "violation",
                        f"Controls lack accessible names: {', '.join(violations)}.",
                    )
                ]
            if incomplete:
                return [
                    _verdict(
                        self,
                        rule,
                        artifact,
                        "inconclusive",
                        f"Accessible names could not be determined: {', '.join(incomplete)}.",
                    )
                ]
            return [_verdict(self, rule, artifact, "pass", "Web controls have accessible names.")]
        missing, valid = ios_missing_accessible_names(artifact.payload)
        if not valid:
            return [
                _verdict(
                    self, rule, artifact, "inconclusive", "iOS accessibility data is incomplete."
                )
            ]
        if missing:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "violation",
                    f"iOS controls lack accessible names: {', '.join(missing[:20])}.",
                )
            ]
        return [_verdict(self, rule, artifact, "pass", "iOS controls have accessible names.")]


class DynamicTypeEvaluator:
    name = "dynamic-type"
    version = "1.0.0"

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind == "text_metadata"

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        supports = artifact.payload.get("supports_dynamic_type")
        clipped = artifact.payload.get("clips_required_text")
        required_categories = rule.parameters.get("required_categories", [])
        tested_categories = artifact.payload.get("tested_categories", [])
        if not isinstance(supports, bool) or not isinstance(clipped, bool):
            return [
                _verdict(self, rule, artifact, "inconclusive", "Text-size metadata is incomplete.")
            ]
        if not all(
            isinstance(items, list) and all(isinstance(item, str) for item in items)
            for items in (required_categories, tested_categories)
        ):
            return [
                _verdict(self, rule, artifact, "inconclusive", "Text-size categories are invalid.")
            ]
        if not supports or clipped or set(required_categories).difference(tested_categories):
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "violation",
                    "Dynamic Type is unsupported or clips required text.",
                )
            ]
        return [_verdict(self, rule, artifact, "pass", "Dynamic Type metadata passed.")]


class IOSAccessibilitySnapshotEvaluator:
    name = "ios-accessibility"
    version = "1.0.0"

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind == "ios_accessibility"

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        if artifact.payload.get("snapshot_complete") is not True:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "inconclusive",
                    "iOS accessibility snapshot is incomplete.",
                )
            ]
        violations = artifact.payload.get("violations", [])
        if not isinstance(violations, list) or not all(
            isinstance(item, str) and item.strip() for item in violations
        ):
            return [
                _verdict(
                    self, rule, artifact, "inconclusive", "iOS accessibility findings are invalid."
                )
            ]
        if violations:
            return [
                _verdict(
                    self,
                    rule,
                    artifact,
                    "violation",
                    f"iOS accessibility violations: {', '.join(sorted(set(violations))[:20])}.",
                )
            ]
        return [_verdict(self, rule, artifact, "pass", "iOS accessibility snapshot passed.")]


class ReducedMotionEvaluator:
    name = "reduced-motion"
    version = "1.0.0"

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind == "motion_metadata"

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        respects = artifact.payload.get("respects_reduced_motion")
        if not isinstance(respects, bool):
            return [
                _verdict(self, rule, artifact, "inconclusive", "Motion metadata is incomplete.")
            ]
        status = "pass" if respects else "violation"
        summary = (
            "Reduced-motion behavior is supported."
            if respects
            else "Motion does not respect the reduced-motion preference."
        )
        return [_verdict(self, rule, artifact, status, summary)]


class ScreenshotReferenceEvaluator:
    name = "screenshot-reference"
    version = "1.0.0"

    def supports(self, artifact: PreferenceArtifact) -> bool:
        return artifact.kind == "screenshot_metadata"

    def evaluate(
        self, rule: PreferenceRule, artifact: PreferenceArtifact
    ) -> list[PreferenceVerdict]:
        width = artifact.payload.get("width")
        height = artifact.payload.get("height")
        current = artifact.payload.get("current_present")
        reference = artifact.payload.get("reference_present", False)
        minimum_width = rule.parameters.get("min_width", 1)
        minimum_height = rule.parameters.get("min_height", 1)
        reference_required = rule.parameters.get("reference_required", False)
        integers = (width, height, minimum_width, minimum_height)
        if (
            not all(
                isinstance(item, int) and not isinstance(item, bool) and item > 0
                for item in integers
            )
            or not isinstance(current, bool)
            or not isinstance(reference, bool)
            or not isinstance(reference_required, bool)
        ):
            return [
                _verdict(self, rule, artifact, "inconclusive", "Screenshot metadata is incomplete.")
            ]
        violations: list[str] = []
        if not current:
            violations.append("current screenshot is missing")
        if width < minimum_width or height < minimum_height:
            violations.append("screenshot dimensions are below the required minimum")
        if reference_required and not reference:
            violations.append("reference screenshot is missing")
        if violations:
            return [_verdict(self, rule, artifact, "violation", "; ".join(violations) + ".")]
        return [_verdict(self, rule, artifact, "pass", "Screenshot metadata passed.")]


def evaluate_artifacts(
    profile: PreferenceProfile,
    artifacts: PreferenceArtifact | Iterable[PreferenceArtifact],
    *,
    evaluators: Iterable[PreferenceEvaluator] | None = None,
) -> list[PreferenceVerdict]:
    artifact_list = [artifacts] if isinstance(artifacts, PreferenceArtifact) else list(artifacts)
    if len(artifact_list) > 4_096:
        raise PreferenceEvaluationError("preference artifact batch exceeds 4096 items")
    artifact_ids = [artifact.artifact_id for artifact in artifact_list]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise PreferenceEvaluationError("preference artifact IDs must be unique")
    configured = list(evaluators) if evaluators is not None else _default_evaluators(profile)
    registry: dict[str, PreferenceEvaluator] = {}
    for evaluator in configured:
        if evaluator.name in registry:
            raise PreferenceEvaluationError(f"duplicate preference evaluator: {evaluator.name}")
        registry[evaluator.name] = evaluator
    verdicts: list[PreferenceVerdict] = []
    for rule in profile.rules:
        if rule.evaluator is None:
            continue
        evaluator = registry.get(rule.evaluator)
        if evaluator is None:
            raise PreferenceEvaluationError(
                f"no evaluator is registered for preference rule {rule.id!r}: {rule.evaluator!r}"
            )
        for artifact in artifact_list:
            if evaluator.supports(artifact):
                verdicts.extend(evaluator.evaluate(rule, artifact))
    return verdicts


def _default_evaluators(profile: PreferenceProfile) -> list[PreferenceEvaluator]:
    return [
        DesignTokenLiteralEvaluator(profile.design_tokens),
        RequiredComponentEvaluator(),
        AxeAccessibilityEvaluator(),
        AccessibleNameEvaluator(),
        DynamicTypeEvaluator(),
        IOSAccessibilitySnapshotEvaluator(),
        ReducedMotionEvaluator(),
        ScreenshotReferenceEvaluator(),
    ]


def _verdict(
    evaluator: PreferenceEvaluator,
    rule: PreferenceRule,
    artifact: PreferenceArtifact,
    status: Literal["pass", "violation", "skipped", "inconclusive"],
    summary: str,
) -> PreferenceVerdict:
    identity = json.dumps(
        [rule.id, artifact.artifact_id, evaluator.name, evaluator.version, status, summary],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return PreferenceVerdict(
        verdict_id=f"verdict-{hashlib.sha256(identity).hexdigest()[:24]}",
        rule_id=rule.id,
        artifact_id=artifact.artifact_id,
        evaluator=evaluator.name,
        evaluator_version=evaluator.version,
        status=status,
        severity=rule.severity,
        summary=summary,
        confidence=1.0,
    )


def _bounded_tree(value: Any, *, depth: int = 0, count: list[int] | None = None) -> None:
    if depth > 16:
        raise ValueError("preference artifact payload exceeds maximum nesting depth")
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > 100_000:
        raise ValueError("preference artifact payload contains too many values")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise ValueError("preference artifact keys must be bounded strings")
            _bounded_tree(child, depth=depth + 1, count=count)
    elif isinstance(value, list):
        for child in value:
            _bounded_tree(child, depth=depth + 1, count=count)
