from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import islice
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TaskType = Literal["search", "edit", "debug", "migration", "review", "ui", "repair"]
Risk = Literal["low", "medium", "high"]
TestScope = Literal["none", "targeted", "broad", "full"]

_TOKEN = re.compile(r"[a-z0-9_+#.-]+")
_TASK_TERMS: dict[TaskType, frozenset[str]] = {
    "migration": frozenset({"migrate", "migration", "schema", "backfill", "alembic"}),
    "ui": frozenset(
        {
            "ui",
            "ux",
            "frontend",
            "react",
            "swiftui",
            "css",
            "responsive",
            "dashboard",
            "visual",
        }
    ),
    "repair": frozenset({"fix", "fixes", "repair", "recover", "heal", "rollback", "restore"}),
    "debug": frozenset(
        {"debug", "bug", "failing", "failure", "error", "traceback", "crash", "broken"}
    ),
    "review": frozenset({"review", "audit", "assess", "inspect", "analyze"}),
    "edit": frozenset(
        {
            "add",
            "build",
            "change",
            "create",
            "implement",
            "refactor",
            "remove",
            "update",
            "write",
        }
    ),
    "search": frozenset({"find", "locate", "search", "where", "which", "explain"}),
}
_CLASSIFICATION_ORDER: tuple[TaskType, ...] = (
    "migration",
    "ui",
    "repair",
    "debug",
    "review",
    "edit",
    "search",
)
_AUTH_TERMS = frozenset({"auth", "authentication", "authorization", "oauth", "login"})
_TOKEN_TERMS = frozenset({"token", "tokens", "credential", "credentials", "secret", "secrets"})
_TOKEN_SECURITY_CONTEXT = frozenset(
    {"access", "api", "auth", "authentication", "bearer", "credential", "refresh", "secret"}
)
_VERIFICATION_TOOLS = frozenset(
    {"build", "lint", "mypy", "pytest", "ruff", "test", "tests", "tsc", "typecheck"}
)
_DATABASE_TERMS = frozenset(
    {"alembic", "backfill", "database", "db", "migration", "migrate", "schema", "sql"}
)
_BROWSER_TERMS = frozenset({"browser", "playwright", "responsive", "visual", "web"})
_VERIFICATION_COMMAND = re.compile(
    r"(?:^|\s)(?:cargo\s+test|dotnet\s+test|go\s+test|npm\s+(?:run\s+)?test|pnpm\s+test|"
    r"pytest|ruff|swift\s+test|tsc|xcodebuild)(?:\s|$)",
    re.IGNORECASE,
)


class RepositorySnapshot(BaseModel):
    """Precomputed, bounded repository metadata; extraction never crawls the filesystem."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    languages: frozenset[str] = Field(default_factory=frozenset, max_length=128)
    file_count: int = Field(default=0, ge=0, le=10_000_000)
    changed_file_count: int = Field(default=0, ge=0, le=1_000_000)
    changed_symbol_count: int = Field(default=0, ge=0, le=10_000_000)
    dependency_depth: int = Field(default=0, ge=0, le=10_000)
    test_scope: TestScope = "none"
    tool_requirements: frozenset[str] = Field(default_factory=frozenset, max_length=128)

    @field_validator("languages", "tool_requirements")
    @classmethod
    def _normalize_tokens(cls, values: frozenset[str]) -> frozenset[str]:
        normalized = frozenset(value.strip().lower() for value in values)
        if "" in normalized or any(len(value) > 128 for value in normalized):
            raise ValueError("repository metadata tokens must be non-empty and bounded")
        return normalized


class TaskProfile(BaseModel):
    """Zero-token features used by the deterministic routing policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: TaskType
    risk: Risk
    complexity_score: int = Field(ge=0, le=100)
    requires: frozenset[str] = Field(max_length=256)
    context_tokens_estimate: int = Field(ge=0, le=2_000_000)
    retry_count: int = Field(ge=0, le=1_000)
    prompt_length: int = Field(ge=0, le=20_000)
    prompt_truncated: bool
    languages: frozenset[str] = Field(max_length=128)
    file_count: int = Field(ge=0, le=10_000_000)
    changed_file_count: int = Field(ge=0, le=1_000_000)
    changed_symbol_count: int = Field(ge=0, le=10_000_000)
    dependency_depth: int = Field(ge=0, le=10_000)
    test_scope: TestScope
    verification_failures: int = Field(ge=0, le=512)
    event_count: int = Field(ge=0, le=512)
    loop_fingerprints: frozenset[str] = Field(max_length=512)
    sensitivity_markers: frozenset[str] = Field(max_length=32)
    task_keywords: frozenset[str] = Field(max_length=256)
    extractor: Literal["deterministic-v1"] = "deterministic-v1"


class FeatureExtractor:
    """Build a routing profile using bounded deterministic rules only."""

    MAX_PROMPT_CHARS = 20_000
    MAX_EVENTS = 512
    MAX_RETRY_COUNT = 1_000
    MAX_CONTEXT_TOKENS = 2_000_000
    _MAX_EVENT_TEXT = 2_000
    _MAX_CONTAINER_ITEMS = 32
    _MAX_NESTING = 4

    def extract(
        self,
        *,
        prompt: str,
        repo: RepositorySnapshot,
        events: Sequence[object],
    ) -> TaskProfile:
        if not isinstance(prompt, str):
            raise TypeError("routing prompt must be text")
        if not isinstance(repo, RepositorySnapshot):
            raise TypeError("repo must be a RepositorySnapshot")
        if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
            raise TypeError("events must be a bounded sequence")

        bounded_prompt = prompt[: self.MAX_PROMPT_CHARS]
        if not bounded_prompt.strip():
            raise ValueError("routing prompt must not be empty")
        tokens = frozenset(_TOKEN.findall(bounded_prompt.lower()))
        task_type, task_keywords = _classify(tokens)
        sensitivity = _sensitivity_markers(tokens)
        trajectory = tuple(events[-self.MAX_EVENTS :])
        fingerprints = [_event_fingerprint(event, self) for event in trajectory]
        counts = Counter(fingerprints)
        loop_fingerprints = frozenset(
            fingerprint for fingerprint, count in counts.items() if count > 1
        )
        duplicate_retries = sum(count - 1 for count in counts.values() if count > 1)
        explicit_retries = max((_explicit_retry_count(event) for event in trajectory), default=0)
        retry_count = min(
            self.MAX_RETRY_COUNT,
            max(duplicate_retries, explicit_retries),
        )
        verification_failures = min(
            self.MAX_EVENTS,
            sum(1 for event in trajectory if _is_verification_failure(event)),
        )
        requires = _requirements(
            task_type=task_type,
            tokens=tokens,
            sensitivity=sensitivity,
            repo=repo,
            verification_failures=verification_failures,
        )
        complexity = _complexity(
            task_type=task_type,
            prompt_length=len(bounded_prompt),
            repo=repo,
            sensitivity_count=len(sensitivity),
            verification_failures=verification_failures,
            retry_count=retry_count,
        )
        risk: Risk
        if (
            sensitivity
            or task_type == "migration"
            or verification_failures >= 2
            or retry_count >= 2
            or complexity >= 75
        ):
            risk = "high"
        elif task_type != "search" or verification_failures or complexity >= 30:
            risk = "medium"
        else:
            risk = "low"
        context_estimate = min(
            self.MAX_CONTEXT_TOKENS,
            (len(bounded_prompt) + 3) // 4
            + min(repo.file_count, 50_000) * 2
            + min(repo.changed_symbol_count, 100_000) * 8
            + len(trajectory) * 64,
        )
        return TaskProfile(
            task_type=task_type,
            risk=risk,
            complexity_score=complexity,
            requires=requires,
            context_tokens_estimate=context_estimate,
            retry_count=retry_count,
            prompt_length=len(bounded_prompt),
            prompt_truncated=len(prompt) > len(bounded_prompt),
            languages=repo.languages,
            file_count=repo.file_count,
            changed_file_count=repo.changed_file_count,
            changed_symbol_count=repo.changed_symbol_count,
            dependency_depth=repo.dependency_depth,
            test_scope=repo.test_scope,
            verification_failures=verification_failures,
            event_count=len(trajectory),
            loop_fingerprints=loop_fingerprints,
            sensitivity_markers=sensitivity,
            task_keywords=task_keywords,
        )


def _classify(tokens: frozenset[str]) -> tuple[TaskType, frozenset[str]]:
    matched: set[str] = set()
    selected: TaskType = "search"
    for task_type in _CLASSIFICATION_ORDER:
        terms = tokens.intersection(_TASK_TERMS[task_type])
        if terms:
            matched.update(terms)
            matched.add(task_type)
            if selected == "search":
                selected = task_type
    return selected, frozenset(matched)


def _sensitivity_markers(tokens: frozenset[str]) -> frozenset[str]:
    markers: set[str] = set()
    if not tokens.isdisjoint(_AUTH_TERMS):
        markers.add("authentication")
    token_matches = tokens.intersection(_TOKEN_TERMS)
    if token_matches and (
        not token_matches.isdisjoint({"credential", "credentials", "secret", "secrets"})
        or not tokens.isdisjoint(_TOKEN_SECURITY_CONTEXT)
    ):
        markers.add("token")
    if "api" in tokens and not tokens.isdisjoint({"key", "keys"}):
        markers.add("token")
    if not tokens.isdisjoint({"permission", "permissions", "privilege", "privileges"}):
        markers.add("permission")
    if not tokens.isdisjoint({"billing", "payment", "payments", "stripe"}) or (
        "card" in tokens and not tokens.isdisjoint({"credit", "debit", "payment"})
    ):
        markers.add("payment")
    if not tokens.isdisjoint({"pii", "privacy", "patient", "medical"}):
        markers.add("privacy")
    if not tokens.isdisjoint({"crypto", "encryption", "keyring", "kms"}):
        markers.add("encryption")
    return frozenset(markers)


def _requirements(
    *,
    task_type: TaskType,
    tokens: frozenset[str],
    sensitivity: frozenset[str],
    repo: RepositorySnapshot,
    verification_failures: int,
) -> frozenset[str]:
    requires = set(repo.tool_requirements)
    if task_type == "search":
        requires.add("search")
    if task_type in {"edit", "debug", "migration", "ui", "repair"}:
        requires.update({"code", "tests"})
    if task_type in {"debug", "repair"} or verification_failures:
        requires.add("verification")
    if not tokens.isdisjoint(_DATABASE_TERMS):
        requires.add("database")
    if task_type == "ui":
        requires.update({"ui", "browser"})
    elif not tokens.isdisjoint(_BROWSER_TERMS):
        requires.add("browser")
    if sensitivity:
        requires.add("security")
    return frozenset(sorted(requires))


def _complexity(
    *,
    task_type: TaskType,
    prompt_length: int,
    repo: RepositorySnapshot,
    sensitivity_count: int,
    verification_failures: int,
    retry_count: int,
) -> int:
    score = {
        "search": 5,
        "review": 20,
        "edit": 25,
        "ui": 35,
        "debug": 40,
        "repair": 45,
        "migration": 55,
    }[task_type]
    score += min(10, prompt_length // 2_000)
    score += min(15, repo.file_count // 1_000)
    score += min(15, repo.changed_file_count // 10)
    score += min(15, repo.changed_symbol_count // 100)
    score += min(15, repo.dependency_depth // 2)
    score += {"none": 0, "targeted": 5, "broad": 10, "full": 20}[repo.test_scope]
    score += min(20, verification_failures * 10)
    score += min(15, retry_count * 5)
    score += min(20, sensitivity_count * 10)
    return min(100, score)


def _event_mapping(event: object) -> Mapping[str, object]:
    if isinstance(event, Mapping):
        return event
    if isinstance(event, BaseModel):
        return {
            key: getattr(event, key)
            for key in (
                "kind",
                "tool_name",
                "tool_args",
                "input_text",
                "output_text",
                "error",
                "metadata",
            )
            if hasattr(event, key)
        }
    raise TypeError("trajectory events must be mappings or Pydantic models")


def _event_fingerprint(event: object, limits: FeatureExtractor) -> str:
    source = _event_mapping(event)
    selected = {
        key: source.get(key)
        for key in ("kind", "tool_name", "tool_args", "input_text", "output_text", "error")
        if source.get(key) is not None
    }
    canonical = json.dumps(
        _bounded_value(selected, limits=limits, depth=0),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _bounded_value(value: object, *, limits: FeatureExtractor, depth: int) -> object:
    if depth >= limits._MAX_NESTING:
        return "<depth-limit>"
    if isinstance(value, str):
        return value[: limits._MAX_EVENT_TEXT]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        entries = list(islice(value.items(), limits._MAX_CONTAINER_ITEMS))
        entries.sort(key=lambda item: str(item[0]))
        return {
            str(key): _bounded_value(child, limits=limits, depth=depth + 1)
            for key, child in entries
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            _bounded_value(item, limits=limits, depth=depth + 1)
            for item in value[: limits._MAX_CONTAINER_ITEMS]
        ]
    return f"<{type(value).__name__}>"


def _explicit_retry_count(event: object) -> int:
    metadata = _event_mapping(event).get("metadata")
    if not isinstance(metadata, Mapping):
        return 0
    value = metadata.get("retry_count", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(FeatureExtractor.MAX_RETRY_COUNT, max(0, value))


def _is_verification_failure(event: object) -> bool:
    source = _event_mapping(event)
    metadata = source.get("metadata")
    if isinstance(metadata, Mapping):
        if metadata.get("verification") is True:
            status = metadata.get("status")
            return not isinstance(status, str) or status.lower() not in {"passed", "success"}
        verification_status = metadata.get("verification_status")
        if isinstance(verification_status, str) and verification_status.lower() in {
            "error",
            "failed",
            "failure",
            "timed_out",
        }:
            return True
    if not _is_verification_tool(source):
        return False
    if bool(source.get("error")):
        return True
    if not isinstance(metadata, Mapping):
        return False
    exit_code = metadata.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return True
    status = metadata.get("status")
    return isinstance(status, str) and status.lower() in {
        "error",
        "failed",
        "failure",
        "timed_out",
    }


def _is_verification_tool(source: Mapping[str, object]) -> bool:
    tool_name = source.get("tool_name")
    if isinstance(tool_name, str) and tool_name.lower() in _VERIFICATION_TOOLS:
        return True
    tool_args = source.get("tool_args")
    if not isinstance(tool_args, Mapping):
        return False
    command = tool_args.get("command", tool_args.get("cmd"))
    return isinstance(command, str) and _VERIFICATION_COMMAND.search(command[:2_000]) is not None
