from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import PreferenceRule


_DEFAULT_AXE_RULES = {
    "wcag-contrast": frozenset({"color-contrast", "color-contrast-enhanced"}),
    "accessible-name": frozenset(
        {
            "aria-input-field-name",
            "button-name",
            "input-button-name",
            "link-name",
            "select-name",
        }
    ),
}
_NAMED_IOS_ROLES = frozenset({"button", "link", "menuitem", "tab", "textfield"})


def axe_rule_ids(rule: PreferenceRule) -> frozenset[str]:
    configured = rule.parameters.get("axe_rule_ids")
    if configured is None:
        return _DEFAULT_AXE_RULES.get(rule.id, frozenset({rule.id}))
    if not isinstance(configured, list) or not all(
        isinstance(item, str) and item.strip() for item in configured
    ):
        return frozenset()
    return frozenset(item.strip() for item in configured)


def axe_findings(
    payload: Mapping[str, Any], relevant: frozenset[str]
) -> tuple[list[str], list[str], bool]:
    violations = _finding_ids(payload.get("violations", []))
    incomplete = _finding_ids(payload.get("incomplete", []))
    if violations is None or incomplete is None or not relevant:
        return [], [], False
    return (
        sorted(relevant.intersection(violations)),
        sorted(relevant.intersection(incomplete)),
        True,
    )


def ios_missing_accessible_names(payload: Mapping[str, Any]) -> tuple[list[str], bool]:
    if payload.get("snapshot_complete") is not True:
        return [], False
    elements = payload.get("elements")
    if not isinstance(elements, list) or len(elements) > 100_000:
        return [], False
    missing: list[str] = []
    for index, element in enumerate(elements):
        if not isinstance(element, Mapping):
            return [], False
        role = element.get("role")
        if role not in _NAMED_IOS_ROLES:
            continue
        label = element.get("label")
        if not isinstance(label, str) or not label.strip():
            identifier = element.get("identifier")
            missing.append(identifier if isinstance(identifier, str) and identifier else str(index))
    return missing, True


def _finding_ids(value: Any) -> set[str] | None:
    if not isinstance(value, list) or len(value) > 100_000:
        return None
    result: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            result.add(item.strip())
        elif isinstance(item, Mapping) and isinstance(item.get("id"), str) and item["id"].strip():
            result.add(item["id"].strip())
        else:
            return None
    return result
