from __future__ import annotations

import pytest

from loopguard.heal.fixtures import FixtureBuilder, UnsafeFixture
from loopguard.heal.schema import diff_schema, schema_of


def test_fixture_redacts_identity_but_preserves_types_and_coordinate_ranges() -> None:
    fixture = FixtureBuilder(key=b"test-redaction-key").build(
        [{"email": "person@example.com", "lat": 43.65, "lon": -79.38}]
    )

    assert fixture.rows[0]["email"] != "person@example.com"
    assert isinstance(fixture.rows[0]["email"], str)
    assert fixture.rows[0]["lat"] != 43.65
    assert fixture.rows[0]["lon"] != -79.38
    assert isinstance(fixture.rows[0]["lat"], float)
    assert isinstance(fixture.rows[0]["lon"], float)
    assert -90 <= fixture.rows[0]["lat"] <= 90
    assert -180 <= fixture.rows[0]["lon"] <= 180
    assert fixture.provenance == "source_redacted"


def test_schema_delta_detects_string_to_float() -> None:
    before = schema_of([{"lat": "north"}])
    after = schema_of([{"lat": 43.65}])

    assert diff_schema(before, after).changes == [{"path": "lat", "from": "string", "to": "float"}]


def test_nested_schema_tracks_nullability_ranges_and_shape() -> None:
    schema = schema_of(
        [
            {"location": {"lat": 43.0, "tags": ["urban"]}, "count": 2},
            {"location": {"lat": None, "tags": ["rural"]}},
        ]
    )

    fields = {field.path: field for field in schema.fields}
    assert fields["location.lat"].nullable is True
    assert fields["location.lat"].minimum == 43.0
    assert fields["count"].nullable is True
    assert fields["location.tags[]"].types == ("string",)
    assert "urban" not in str(schema)
    assert "rural" not in str(schema)


def test_redaction_is_deterministic_and_manifest_never_contains_source_values() -> None:
    builder = FixtureBuilder(key=b"test-redaction-key")
    source = [
        {
            "user_id": "customer-123",
            "created_at": "2026-07-30T18:15:00Z",
            "notes": "Unique private support request",
            "category": "one-off-category",
            "payload": "Bearer ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        }
    ]

    first = builder.build(source)
    second = builder.build(source)

    assert first.rows == second.rows
    assert first.rows[0] != source[0]
    manifest_text = str(first.redactions)
    assert "customer-123" not in manifest_text
    assert "Unique private support request" not in manifest_text
    assert "ghp_" not in str(first.rows)


def test_string_coordinate_remains_a_non_numeric_string_for_reproduction() -> None:
    fixture = FixtureBuilder(key=b"test-redaction-key").build([{"lat": "north", "lon": "-79.38"}])

    assert isinstance(fixture.rows[0]["lat"], str)
    assert fixture.rows[0]["lat"] != "north"
    with pytest.raises(ValueError):
        float(fixture.rows[0]["lat"])


def test_input_and_output_limits_fail_closed() -> None:
    with pytest.raises(UnsafeFixture, match="row limit"):
        FixtureBuilder(max_rows=1).build([{"value": 1}, {"value": 2}])

    with pytest.raises(UnsafeFixture, match="byte limit"):
        FixtureBuilder(max_bytes=32).build([{"value": "x" * 100}])

    with pytest.raises(UnsafeFixture, match="finite"):
        FixtureBuilder().build([{"value": float("nan")}])


def test_empty_source_can_only_use_explicit_schema_for_synthetic_fixture() -> None:
    with pytest.raises(UnsafeFixture, match="safe fixture"):
        FixtureBuilder().build([])

    schema = schema_of([{"lat": "north", "count": 2, "enabled": True}])
    fixture = FixtureBuilder(key=b"test-redaction-key").build([], schema=schema)

    assert fixture.provenance == "schema_synthetic"
    assert fixture.source_row_count == 0
    assert isinstance(fixture.rows[0]["lat"], str)
    assert isinstance(fixture.rows[0]["count"], int)
    assert isinstance(fixture.rows[0]["enabled"], bool)


def test_csv_values_are_inferred_then_redacted() -> None:
    fixture = FixtureBuilder(key=b"test-redaction-key").build_csv(
        "email,count,lat\nperson@example.com,2,43.65\n"
    )

    assert isinstance(fixture.rows[0]["count"], int)
    assert isinstance(fixture.rows[0]["lat"], float)
    assert fixture.rows[0]["email"] != "person@example.com"
