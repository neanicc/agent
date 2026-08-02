"""Privacy-preserving replay fixture construction."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import math
import re
import secrets
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from loopguard.heal.models import RepairModel
from loopguard.heal.schema import DatasetSchema, FieldSchema, schema_of


class UnsafeFixture(ValueError):
    pass


class FixtureRedaction(RepairModel):
    path: str = Field(min_length=1, max_length=2_048)
    transformation: str = Field(min_length=1, max_length=128)
    count: int = Field(gt=0)


class ReplayFixture(RepairModel):
    rows: tuple[dict[str, Any], ...]
    inferred_schema: DatasetSchema = Field(serialization_alias="schema")
    redactions: tuple[FixtureRedaction, ...]
    provenance: Literal["source_redacted", "schema_synthetic"]
    source_row_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


_EMAIL = re.compile(r"(?i)^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._-]{16,}|gh[pousr]_[a-z0-9]{20,}|"
    r"sk-[a-z0-9_-]{20,}|xox[baprs]-[a-z0-9-]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_INTEGER = re.compile(r"^-?(?:0|[1-9]\d*)$")
_FLOAT = re.compile(r"^-?(?:0|[1-9]\d*)\.\d+(?:[eE][+-]?\d+)?$")
_LATITUDE_NAMES = {"lat", "latitude"}
_LONGITUDE_NAMES = {"lon", "lng", "longitude"}
_IDENTITY_NAMES = {
    "address",
    "age",
    "birthdate",
    "city",
    "customer",
    "device_id",
    "email",
    "first_name",
    "full_name",
    "id",
    "ip",
    "last_name",
    "name",
    "phone",
    "postal_code",
    "ssn",
    "user_id",
    "uuid",
    "zip",
    "zipcode",
}
_TEXT_NAMES = {"comment", "description", "message", "note", "notes", "text"}
_TIME_NAMES = {"created_at", "date", "datetime", "event_time", "timestamp", "updated_at"}
_SECRET_NAMES = {"api_key", "authorization", "password", "private_key", "secret", "token"}


class FixtureBuilder:
    def __init__(
        self,
        *,
        key: bytes | None = None,
        max_rows: int = 100,
        max_bytes: int = 1_048_576,
    ) -> None:
        if max_rows < 1 or max_bytes < 1:
            raise ValueError("fixture limits must be positive")
        self._key = key or secrets.token_bytes(32)
        if len(self._key) < 16:
            raise ValueError("fixture redaction key must contain at least 16 bytes")
        self.max_rows = max_rows
        self.max_bytes = max_bytes

    def build(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        schema: DatasetSchema | None = None,
    ) -> ReplayFixture:
        if len(rows) > self.max_rows:
            raise UnsafeFixture("fixture row limit exceeded")
        if not rows:
            if schema is None:
                raise UnsafeFixture("no safe fixture exists without rows or an explicit schema")
            synthetic = self._synthetic_row(schema)
            return self._finish(
                [synthetic],
                redactions={},
                provenance="schema_synthetic",
                source_row_count=0,
            )

        source = [self._bounded_row(row) for row in rows]
        self._check_bytes(source, label="input")
        rare_paths = _rare_string_paths(source)
        redactions: dict[tuple[str, str], int] = defaultdict(int)
        transformed = [
            self._redact(
                row,
                path="",
                rare_paths=rare_paths,
                redactions=redactions,
                depth=0,
            )
            for row in source
        ]
        assert all(isinstance(row, dict) for row in transformed)
        return self._finish(
            transformed,
            redactions=redactions,
            provenance="source_redacted",
            source_row_count=len(rows),
        )

    def build_csv(self, text: str) -> ReplayFixture:
        encoded = text.encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise UnsafeFixture("fixture byte limit exceeded")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None or len(reader.fieldnames) > 128:
            raise UnsafeFixture("CSV header is invalid")
        rows: list[dict[str, Any]] = []
        for row in reader:
            if len(rows) >= self.max_rows:
                raise UnsafeFixture("fixture row limit exceeded")
            rows.append({key: _coerce_csv(value) for key, value in row.items()})
        return self.build(rows)

    def _redact(
        self,
        value: Any,
        *,
        path: str,
        rare_paths: frozenset[str],
        redactions: dict[tuple[str, str], int],
        depth: int,
    ) -> Any:
        if depth > 16:
            raise UnsafeFixture("fixture nesting is too deep")
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                    raise UnsafeFixture("nested fixture field name is invalid")
                result[key] = self._redact(
                    child,
                    path=f"{path}.{key}" if path else key,
                    rare_paths=rare_paths,
                    redactions=redactions,
                    depth=depth + 1,
                )
            return result
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [
                self._redact(
                    child,
                    path=f"{path}[]",
                    rare_paths=rare_paths,
                    redactions=redactions,
                    depth=depth + 1,
                )
                for child in value
            ]
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, float) and not math.isfinite(value):
            raise UnsafeFixture("fixture numbers must be finite")

        field_name = _field_name(path)
        transformation: str | None = None
        replacement = value
        if field_name in _SECRET_NAMES or (isinstance(value, str) and _SECRET.search(value)):
            transformation = "secret_token"
            replacement = self._secret_value(path, value)
        elif field_name in _LATITUDE_NAMES:
            transformation = "coordinate_bucket"
            replacement = self._coordinate(path, value, latitude=True)
        elif field_name in _LONGITUDE_NAMES:
            transformation = "coordinate_bucket"
            replacement = self._coordinate(path, value, latitude=False)
        elif field_name in _TIME_NAMES and isinstance(value, str):
            transformation = "timestamp_bucket"
            replacement = self._timestamp(path, value)
        elif (
            field_name in _IDENTITY_NAMES
            or field_name.endswith("_id")
            or (isinstance(value, str) and _EMAIL.fullmatch(value))
        ):
            transformation = "identity_token"
            replacement = self._identity(path, value)
        elif field_name in _TEXT_NAMES and isinstance(value, str):
            transformation = "free_text_token"
            replacement = f"text_{self._token(path, value)}"
        elif isinstance(value, str) and path in rare_paths:
            transformation = "rare_category_token"
            replacement = f"category_{self._token(path, value)}"

        if transformation is not None:
            redactions[(path, transformation)] += 1
        return replacement

    def _coordinate(self, path: str, value: object, *, latitude: bool) -> object:
        if isinstance(value, str):
            return f"redacted-coordinate-{self._token(path, value)}"
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise UnsafeFixture("coordinate must be a finite number or string")
        limit = 90 if latitude else 180
        digest = int(self._token(path, value), 16)
        offset = 0.5 + (digest % 1_500) / 1_000
        if digest % 2:
            offset *= -1
        shifted = float(value) + offset
        if latitude:
            shifted = max(-90.0, min(90.0, shifted))
            if shifted == float(value):
                shifted = float(value) - offset
        else:
            shifted = ((shifted + 180.0) % 360.0) - 180.0
        if isinstance(value, int):
            rounded = int(round(shifted))
            if rounded == value:
                rounded = max(-limit, min(limit, value + (1 if offset > 0 else -1)))
            return rounded
        return round(shifted, 6)

    def _timestamp(self, path: str, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return f"timestamp_{self._token(path, value)}"
        bucket = parsed.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rendered = bucket.isoformat()
        normalized = rendered.replace("+00:00", "Z")
        if normalized == value:
            normalized = bucket.replace(day=2).isoformat().replace("+00:00", "Z")
        return normalized

    def _identity(self, path: str, value: object) -> object:
        if isinstance(value, str):
            if _EMAIL.fullmatch(value):
                return f"person-{self._token(path, value)}@example.invalid"
            return f"id_{self._token(path, value)}"
        if isinstance(value, int) and not isinstance(value, bool):
            shift = 1 + int(self._token(path, value), 16) % 7
            return value + shift
        if isinstance(value, float) and math.isfinite(value):
            return round(value + 0.5, 6)
        raise UnsafeFixture("identity field has an unsupported type")

    def _secret_value(self, path: str, value: object) -> object:
        if isinstance(value, str):
            return f"secret_{self._token(path, value)}"
        if isinstance(value, int) and not isinstance(value, bool):
            return int(self._token(path, value), 16) % 1_000_000
        if isinstance(value, float) and math.isfinite(value):
            return float(int(self._token(path, value), 16) % 1_000_000)
        raise UnsafeFixture("secret field has an unsupported type")

    def _token(self, path: str, value: object) -> str:
        canonical = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hmac.new(
            self._key,
            f"{path}\0{canonical}".encode(),
            hashlib.sha256,
        ).hexdigest()[:12]

    def _synthetic_row(self, schema: DatasetSchema) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for field in schema.fields:
            if "." in field.path or "[]" in field.path or field.types == ("object",):
                continue
            row[field.path] = _synthetic_value(field)
        if not row:
            raise UnsafeFixture("schema cannot produce a bounded synthetic fixture")
        return row

    def _finish(
        self,
        rows: list[dict[str, Any]],
        *,
        redactions: Mapping[tuple[str, str], int],
        provenance: Literal["source_redacted", "schema_synthetic"],
        source_row_count: int,
    ) -> ReplayFixture:
        self._check_bytes(rows, label="output")
        if _contains_secret(rows):
            raise UnsafeFixture("fixture contains an unredacted known secret")
        encoded = json.dumps(
            rows,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        manifest = tuple(
            FixtureRedaction(path=path, transformation=transformation, count=count)
            for (path, transformation), count in sorted(redactions.items())
        )
        return ReplayFixture(
            rows=tuple(rows),
            inferred_schema=schema_of(rows),
            redactions=manifest,
            provenance=provenance,
            source_row_count=source_row_count,
            content_sha256=hashlib.sha256(encoded).hexdigest(),
        )

    def _bounded_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(row, Mapping) or len(row) > 512:
            raise UnsafeFixture("fixture row must be a bounded object")
        result: dict[str, Any] = {}
        for key, value in row.items():
            if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                raise UnsafeFixture("fixture field name is invalid")
            result[key] = value
        return result

    def _check_bytes(self, value: object, *, label: str) -> None:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        except (TypeError, ValueError) as exc:
            raise UnsafeFixture(f"fixture {label} is not valid JSON") from exc
        if len(encoded) > self.max_bytes:
            raise UnsafeFixture("fixture byte limit exceeded")


def _rare_string_paths(rows: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    values: dict[str, list[str]] = defaultdict(list)

    def collect(value: object, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                collect(child, f"{path}.{key}" if path else key)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                collect(child, f"{path}[]")
        elif isinstance(value, str):
            values[path].append(value)

    for row in rows:
        collect(row, "")
    rare: set[str] = set()
    for path, observed in values.items():
        counts = Counter(observed)
        if any(count == 1 for count in counts.values()) and len(counts) <= 20:
            rare.add(path)
    return frozenset(rare)


def _field_name(path: str) -> str:
    return path.removesuffix("[]").rsplit(".", 1)[-1].lower()


def _coerce_csv(value: str | None) -> object:
    if value is None or value == "":
        return None
    normalized = value.strip()
    if normalized.lower() == "true":
        return True
    if normalized.lower() == "false":
        return False
    if _INTEGER.fullmatch(normalized):
        return int(normalized)
    if _FLOAT.fullmatch(normalized):
        parsed = float(normalized)
        if not math.isfinite(parsed):
            raise UnsafeFixture("CSV number must be finite")
        return parsed
    return normalized


def _contains_secret(value: object) -> bool:
    if isinstance(value, str):
        return _SECRET.search(value) is not None
    if isinstance(value, Mapping):
        return any(_contains_secret(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret(child) for child in value)
    return False


def _synthetic_value(field: FieldSchema) -> object:
    kind = field.types[0]
    if kind == "boolean":
        return False
    if kind == "integer":
        return 0
    if kind == "float":
        return 0.0
    if kind == "string":
        return "synthetic"
    if kind == "array":
        return []
    if kind == "null":
        return None
    return {}
