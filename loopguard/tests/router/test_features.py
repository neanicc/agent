from __future__ import annotations

from loopguard.router.features import FeatureExtractor


def test_security_migration_is_high_risk_without_model_call(repo_snapshot) -> None:
    profile = FeatureExtractor().extract(
        prompt="Migrate authentication tokens and update the database schema",
        repo=repo_snapshot,
        events=[],
    )

    assert profile.task_type == "migration"
    assert profile.risk == "high"
    assert profile.requires == {"code", "tests", "database", "security"}
    assert profile.sensitivity_markers == {"authentication", "token"}
    assert profile.extractor == "deterministic-v1"


def test_simple_search_stays_low_cost_and_low_risk(repo_snapshot) -> None:
    profile = FeatureExtractor().extract(
        prompt="Find where the retry timeout is defined",
        repo=repo_snapshot.model_copy(
            update={
                "changed_file_count": 0,
                "changed_symbol_count": 0,
                "dependency_depth": 1,
                "test_scope": "none",
                "tool_requirements": frozenset({"search"}),
            }
        ),
        events=[],
    )

    assert profile.task_type == "search"
    assert profile.risk == "low"
    assert profile.requires == {"search"}
    assert profile.complexity_score < 30


def test_routine_edit_requires_code_and_tests(repo_snapshot) -> None:
    profile = FeatureExtractor().extract(
        prompt="Update the parser and add unit tests",
        repo=repo_snapshot,
        events=[],
    )

    assert profile.task_type == "edit"
    assert profile.risk == "medium"
    assert {"code", "tests"}.issubset(profile.requires)
    assert profile.languages == {"python", "typescript"}
    assert profile.changed_symbol_count == 4


def test_ui_work_records_browser_and_visual_requirements(repo_snapshot) -> None:
    profile = FeatureExtractor().extract(
        prompt="Redesign the responsive React dashboard and visually test it in a browser",
        repo=repo_snapshot,
        events=[],
    )

    assert profile.task_type == "ui"
    assert {"code", "tests", "ui", "browser"}.issubset(profile.requires)
    assert "ui" in profile.task_keywords


def test_failed_verification_escalates_debug_trajectory(repo_snapshot) -> None:
    events = [
        {"kind": "tool_result", "tool_name": "pytest", "metadata": {"exit_code": 1}},
        {
            "kind": "error",
            "error": "Assertion failed",
            "metadata": {"verification_status": "failed"},
        },
    ]

    profile = FeatureExtractor().extract(
        prompt="Debug the failing checkout test",
        repo=repo_snapshot,
        events=events,
    )

    assert profile.task_type == "debug"
    assert profile.risk == "high"
    assert profile.verification_failures == 2
    assert "verification" in profile.requires


def test_repeated_events_produce_stable_loop_fingerprints_and_retry_count(
    repo_snapshot,
) -> None:
    repeated = {
        "kind": "tool_call",
        "tool_name": "read_file",
        "tool_args": {"path": "src/missing.py"},
        "error": "not found",
    }
    events = [repeated, dict(repeated), dict(repeated)]
    extractor = FeatureExtractor()

    first = extractor.extract(
        prompt="Repair the repeated failure", repo=repo_snapshot, events=events
    )
    second = extractor.extract(
        prompt="Repair the repeated failure", repo=repo_snapshot, events=events
    )

    assert first == second
    assert first.task_type == "repair"
    assert first.retry_count == 2
    assert len(first.loop_fingerprints) == 1
    assert first.verification_failures == 0
    assert first.risk == "high"


def test_token_budget_language_is_not_misclassified_as_a_secret(repo_snapshot) -> None:
    profile = FeatureExtractor().extract(
        prompt="Update the token budget estimate for model routing",
        repo=repo_snapshot,
        events=[],
    )

    assert profile.task_type == "edit"
    assert profile.sensitivity_markers == set()
    assert profile.risk == "medium"


def test_shell_verification_failure_is_detected_but_generic_tool_error_is_not(
    repo_snapshot,
) -> None:
    profile = FeatureExtractor().extract(
        prompt="Debug the command failure",
        repo=repo_snapshot,
        events=[
            {
                "kind": "tool_call",
                "tool_name": "shell",
                "tool_args": {"command": "npm test -- --runInBand"},
                "error": "exit 1",
            },
            {
                "kind": "tool_call",
                "tool_name": "read_file",
                "tool_args": {"path": "missing.py"},
                "error": "not found",
            },
        ],
    )

    assert profile.verification_failures == 1


def test_extraction_is_bounded_for_large_prompt_and_event_trajectory(repo_snapshot) -> None:
    events = [
        {
            "kind": "tool_call",
            "tool_name": "shell",
            "tool_args": {"payload": "x" * 100_000},
            "metadata": {"retry_count": index},
        }
        for index in range(700)
    ]

    profile = FeatureExtractor().extract(
        prompt="update " + "x" * 100_000,
        repo=repo_snapshot,
        events=events,
    )

    assert profile.prompt_length == FeatureExtractor.MAX_PROMPT_CHARS
    assert profile.prompt_truncated is True
    assert profile.event_count == FeatureExtractor.MAX_EVENTS
    assert profile.retry_count <= FeatureExtractor.MAX_RETRY_COUNT
    assert profile.context_tokens_estimate <= FeatureExtractor.MAX_CONTEXT_TOKENS


def test_repository_complexity_and_full_test_scope_raise_complexity(repo_snapshot) -> None:
    complex_repo = repo_snapshot.model_copy(
        update={
            "file_count": 50_000,
            "changed_file_count": 500,
            "changed_symbol_count": 5_000,
            "dependency_depth": 100,
            "test_scope": "full",
        }
    )

    profile = FeatureExtractor().extract(
        prompt="Review this change",
        repo=complex_repo,
        events=[],
    )

    assert profile.task_type == "review"
    assert profile.complexity_score == 100
    assert profile.risk == "high"
    assert profile.test_scope == "full"
