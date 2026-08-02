"""Bounded structural schema inference and deterministic deltas for replay data."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from loopguard.heal.models import RepairModel

_TYPE_ORDER = {
    "null": 0,
    "boolean": 1,
    "integer": 2,
    "float": 3,
    "string": 4,
    "array": 5,
    "object": 6,
}
FieldType = Literal["null", "boolean", "integer", "float", "string", "array", "object"]


class FieldSchema(RepairModel):
    path: str = Field(min_length=1, max_length=2_048)
    types: tuple[FieldType, ...] = Field(min_length=1)
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[str | int | float | bool, ...] = ()


class DatasetSchema(RepairModel):
    fields: tuple[FieldSchema, ...]
    row_count: int = Field(ge=0)


class SchemaDelta(RepairModel):
    changes: list[dict[str, str]] = Field(default_factory=list, max_length=2_048)
    breaking: bool = False


@dataclass(slots=True)
class _Stats:
    types: set[str] = field(default_factory=set)
    numeric: list[float] = field(default_factory=list)
    scalar: list[str | int | float | bool] = field(default_factory=list)
    present_rows: set[int] = field(default_factory=set)


def schema_of(rows: Sequence[Mapping[str, Any]]) -> DatasetSchema:
    if len(rows) > 10_000:
        raise ValueError("schema row limit exceeded")
    stats: dict[str, _Stats] = {}
    for row_index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError("schema rows must be objects")
        for key, value in row.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise ValueError("schema field name is invalid")
            _visit(value, key, row_index, stats, depth=0)

    fields: list[FieldSchema] = []
    for path, values in sorted(stats.items()):
        observed_types = values.types - {"null"}
        types = tuple(sorted(observed_types or {"null"}, key=_TYPE_ORDER.__getitem__))
        enum = _enum(values.scalar)
        fields.append(
            FieldSchema(
                path=path,
                types=types,
                nullable="null" in values.types or len(values.present_rows) < len(rows),
                minimum=min(values.numeric) if values.numeric else None,
                maximum=max(values.numeric) if values.numeric else None,
                enum=enum,
            )
        )
    return DatasetSchema(fields=tuple(fields), row_count=len(rows))


def diff_schema(before: DatasetSchema, after: DatasetSchema) -> SchemaDelta:
    old = {field.path: field for field in before.fields}
    new = {field.path: field for field in after.fields}
    changes: list[dict[str, str]] = []
    breaking = False
    for path in sorted(old.keys() | new.keys()):
        previous = old.get(path)
        current = new.get(path)
        if previous is None and current is not None:
            changes.append({"path": path, "from": "missing", "to": _type_label(current)})
            continue
        if previous is not None and current is None:
            changes.append({"path": path, "from": _type_label(previous), "to": "missing"})
            breaking = True
            continue
        assert previous is not None and current is not None
        if previous.types != current.types:
            changes.append(
                {
                    "path": path,
                    "from": _type_label(previous),
                    "to": _type_label(current),
                }
            )
            breaking = True
        if previous.nullable and not current.nullable:
            changes.append({"path": path, "from": "nullable", "to": "required"})
            breaking = True
    return SchemaDelta(changes=changes, breaking=breaking)


def _visit(
    value: Any,
    path: str,
    row_index: int,
    stats: dict[str, _Stats],
    *,
    depth: int,
) -> None:
    if depth > 16:
        raise ValueError("schema nesting is too deep")
    current = stats.setdefault(path, _Stats())
    current.present_rows.add(row_index)
    kind = _kind(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("schema numbers must be finite")
    if isinstance(value, str) and len(value) > 65_536:
        raise ValueError("schema string is too large")
    current.types.add(kind)
    if kind in {"integer", "float"}:
        current.numeric.append(float(value))
        current.scalar.append(value)
    elif kind in {"string", "boolean"}:
        current.scalar.append(value)
    if isinstance(value, Mapping):
        if len(value) > 512:
            raise ValueError("schema object is too large")
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise ValueError("schema field name is invalid")
            _visit(child, f"{path}.{key}", row_index, stats, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 1_000:
            raise ValueError("schema array is too large")
        for child in value:
            _visit(child, f"{path}[]", row_index, stats, depth=depth + 1)


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    raise ValueError(f"unsupported schema value type: {type(value).__name__}")


def _enum(values: list[str | int | float | bool]) -> tuple[str | int | float | bool, ...]:
    encoded: dict[str, str | int | float | bool] = {}
    for value in values:
        safe_value: str | int | float | bool
        if isinstance(value, str):
            safe_value = f"string:{hashlib.sha256(value.encode()).hexdigest()[:12]}"
        else:
            safe_value = value
        encoded[json.dumps(safe_value, sort_keys=True)] = safe_value
    if not encoded or len(encoded) > 20:
        return ()
    return tuple(encoded[key] for key in sorted(encoded))


def _type_label(field: FieldSchema) -> str:
    return "|".join(field.types)
