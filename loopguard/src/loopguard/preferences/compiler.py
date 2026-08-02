from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import TypeAdapter, ValidationError

from .models import (
    Identifier,
    PreferenceProfile,
    PreferenceRule,
    PreferenceSourceManifest,
    RuleSeverity,
    RuleSource,
)


_MAX_SOURCE_BYTES = 256 * 1024
_MAX_SECTION_BYTES = 32 * 1024
_MAX_RULES_PER_SOURCE = 1_024
_ALLOWED_CONFIG_KEYS = {"schema_version", "design_token_files", "rules"}
_ALLOWED_PROFILE_KEYS = {"rules", "deleted_rule_ids"}
_RULE_LINE = re.compile(
    r"^\s*-\s+([a-z0-9][a-z0-9._:-]{0,127})\s+"
    r"\[(block|warn|inform)\]\s*:\s*(\S(?:.*\S)?)\s*$",
    re.IGNORECASE,
)
_SEVERITY = {RuleSeverity.INFORM: 0, RuleSeverity.WARN: 1, RuleSeverity.BLOCK: 2}
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


class PreferenceCompileError(ValueError):
    """A preference source is malformed, unsafe, or exceeds a declared bound."""


class PreferenceCompiler:
    def compile(
        self,
        *,
        repo: str | Path,
        user_profile: Mapping[str, Any] | None = None,
        organization_policy: Mapping[str, Any] | None = None,
        learned_rules: Iterable[PreferenceRule | Mapping[str, Any]] = (),
    ) -> PreferenceProfile:
        root = _repository_root(Path(repo))
        manifest: list[PreferenceSourceManifest] = []
        tokens: dict[str, Any] = {}

        defaults_bytes = (
            resources.files("loopguard.preferences").joinpath("defaults.toml").read_bytes()
        )
        defaults = _parse_config(defaults_bytes, locator="preferences/defaults.toml")
        rules = {rule.id: rule for rule in _parse_rules(defaults.get("rules", []), "builtin")}
        manifest.append(
            _manifest(
                "builtin-defaults", "builtin", "preferences/defaults.toml", defaults_bytes, True
            )
        )

        config_directory = root / ".loopguard"
        if config_directory.is_symlink():
            raise PreferenceCompileError("preference source path cannot contain symlinks")
        config_path = config_directory / "preferences.toml"
        repository_rules: list[PreferenceRule] = []
        if config_path.exists() or config_path.is_symlink():
            config_bytes = _read_repo_file(root, config_path, _MAX_SOURCE_BYTES)
            config = _parse_config(config_bytes, locator=".loopguard/preferences.toml")
            repository_rules.extend(
                _parse_rules(
                    config.get("rules", []),
                    ".loopguard/preferences.toml",
                    source=RuleSource.REPOSITORY,
                    clamp_block=True,
                )
            )
            manifest.append(
                _manifest(
                    "repository-config",
                    "repository",
                    ".loopguard/preferences.toml",
                    config_bytes,
                    False,
                )
            )
            token_files = config.get("design_token_files", [])
            if len(token_files) != len(set(token_files)):
                raise PreferenceCompileError("design token file paths must be unique")
            for index, relative in enumerate(token_files):
                if not isinstance(relative, str) or not relative.strip():
                    raise PreferenceCompileError("design token paths must be non-empty strings")
                token_path = _safe_relative_file(root, relative)
                token_bytes = _read_repo_file(root, token_path, _MAX_SOURCE_BYTES)
                document = _parse_tokens(token_path, token_bytes)
                for name, value in _flatten_tokens(document).items():
                    if name in tokens:
                        raise PreferenceCompileError(f"duplicate design token: {name}")
                    tokens[name] = value
                manifest.append(
                    _manifest(
                        f"design-token-{index + 1}",
                        "design_tokens",
                        token_path.relative_to(root).as_posix(),
                        token_bytes,
                        False,
                    )
                )

        instruction_rules, instruction_sources = _instruction_rules(root)
        repository_rules.extend(instruction_rules)
        repository_ids = [rule.id for rule in repository_rules]
        if len(repository_ids) != len(set(repository_ids)):
            raise PreferenceCompileError("repository sources contain duplicate rule IDs")
        manifest.extend(instruction_sources)

        organization = _bounded_mapping(organization_policy or {}, "organization policy")
        _reject_unknown_keys(organization, _ALLOWED_PROFILE_KEYS, "organization policy")
        organization_rules = _parse_rules(
            organization.get("rules", []), "organization policy", source=RuleSource.SAFETY
        )
        if organization:
            encoded = _canonical(organization)
            manifest.append(
                _manifest(
                    "organization-policy", "organization", "daemon:organization", encoded, True
                )
            )
        for candidate in organization_rules:
            current = rules.get(candidate.id)
            if current is None or current.source is not RuleSource.SAFETY:
                raise PreferenceCompileError(
                    "organization policy may only strengthen known built-in safety rules"
                )
            if _SEVERITY[candidate.severity] > _SEVERITY[current.severity]:
                rules[candidate.id] = candidate.model_copy(update={"source": RuleSource.SAFETY})

        for candidate in repository_rules:
            current = rules.get(candidate.id)
            if current is None or current.source is not RuleSource.SAFETY:
                rules[candidate.id] = candidate

        user = _bounded_mapping(user_profile or {}, "user profile")
        _reject_unknown_keys(user, _ALLOWED_PROFILE_KEYS, "user profile")
        user_rules = _parse_rules(user.get("rules", []), "user profile", source=RuleSource.EXPLICIT)
        deleted_ids = _parse_deleted_ids(user.get("deleted_rule_ids", []))
        if user:
            encoded = _canonical(user)
            manifest.append(_manifest("user-profile", "user", "daemon:user-profile", encoded, True))
        for candidate in user_rules:
            current = rules.get(candidate.id)
            if current is None or current.source is not RuleSource.SAFETY:
                rules[candidate.id] = candidate
        for rule_id in deleted_ids:
            current = rules.get(rule_id)
            if current is not None and current.source is not RuleSource.SAFETY:
                rules.pop(rule_id)

        learned = _parse_rules(
            [
                item.model_dump(mode="json") if isinstance(item, PreferenceRule) else item
                for item in learned_rules
            ],
            "learned preference store",
            source=RuleSource.LEARNED,
        )
        if learned:
            encoded = _canonical([rule.model_dump(mode="json") for rule in learned])
            manifest.append(
                _manifest("learned-rules", "learned", "store:learned-rules", encoded, True)
            )
        for candidate in learned:
            rules.setdefault(candidate.id, candidate)

        ordered = sorted(rules.values(), key=lambda item: (_source_order(item.source), item.id))
        profile_payload = {
            "rules": [rule.model_dump(mode="json") for rule in ordered],
            "design_tokens": tokens,
            "source_manifest": [item.model_dump(mode="json") for item in manifest],
        }
        profile_id = f"profile-{hashlib.sha256(_canonical(profile_payload)).hexdigest()[:24]}"
        return PreferenceProfile(
            profile_id=profile_id,
            rules=ordered,
            design_tokens=tokens,
            source_manifest=manifest,
        )


def _parse_config(raw: bytes, *, locator: str) -> dict[str, Any]:
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PreferenceCompileError(f"{locator} is not valid bounded TOML") from exc
    _reject_unknown_keys(value, _ALLOWED_CONFIG_KEYS, locator)
    if value.get("schema_version", 1) != 1:
        raise PreferenceCompileError(f"{locator} has an unsupported schema version")
    token_files = value.get("design_token_files", [])
    if (
        not isinstance(token_files, list)
        or len(token_files) > 128
        or not all(isinstance(item, str) for item in token_files)
    ):
        raise PreferenceCompileError(f"{locator} design token file list is invalid")
    return value


def _parse_rules(
    raw_rules: Any,
    locator: str,
    *,
    source: RuleSource | None = None,
    clamp_block: bool = False,
) -> list[PreferenceRule]:
    if not isinstance(raw_rules, list) or len(raw_rules) > _MAX_RULES_PER_SOURCE:
        raise PreferenceCompileError(f"{locator} rules must be a bounded list")
    parsed: list[PreferenceRule] = []
    for raw in raw_rules:
        if not isinstance(raw, Mapping):
            raise PreferenceCompileError(f"{locator} contains a non-object rule")
        candidate = dict(raw)
        if source is not None:
            candidate["source"] = source.value
        if clamp_block and candidate.get("severity") == RuleSeverity.BLOCK.value:
            candidate["severity"] = RuleSeverity.WARN.value
        try:
            parsed.append(PreferenceRule.model_validate(candidate))
        except ValidationError as exc:
            raise PreferenceCompileError(f"{locator} contains an invalid preference rule") from exc
    ids = [rule.id for rule in parsed]
    if len(ids) != len(set(ids)):
        raise PreferenceCompileError(f"{locator} contains duplicate rule IDs")
    return parsed


def _instruction_rules(
    root: Path,
) -> tuple[list[PreferenceRule], list[PreferenceSourceManifest]]:
    rules: list[PreferenceRule] = []
    manifests: list[PreferenceSourceManifest] = []
    for filename in ("AGENTS.md", "CLAUDE.md"):
        path = root / filename
        if not path.exists() and not path.is_symlink():
            continue
        raw = _read_repo_file(root, path, _MAX_SOURCE_BYTES)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PreferenceCompileError(f"{filename} is not UTF-8") from exc
        section = _design_section(text)
        if section is None:
            continue
        if len(section.encode("utf-8")) > _MAX_SECTION_BYTES:
            raise PreferenceCompileError(f"{filename} design preference section exceeds 32 KiB")
        source_rules: list[PreferenceRule] = []
        for line in section.splitlines():
            if not line.strip():
                continue
            match = _RULE_LINE.fullmatch(line)
            if match is None:
                raise PreferenceCompileError(f"{filename} has an unsupported preference line")
            severity = match.group(2).lower()
            if severity == RuleSeverity.BLOCK.value:
                severity = RuleSeverity.WARN.value
            source_rules.append(
                PreferenceRule(
                    id=match.group(1).lower(),
                    source=RuleSource.REPOSITORY,
                    severity=severity,
                    statement=match.group(3),
                )
            )
        ids = [rule.id for rule in source_rules]
        if len(ids) != len(set(ids)) or set(ids).intersection(rule.id for rule in rules):
            raise PreferenceCompileError("instruction sources contain duplicate rule IDs")
        rules.extend(source_rules)
        manifests.append(
            _manifest(
                f"instructions-{filename.split('.')[0].lower()}",
                "instructions",
                filename,
                raw,
                False,
            )
        )
    return rules, manifests


def _design_section(text: str) -> str | None:
    lines = text.splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if re.fullmatch(r"##\s+Design preferences\s*", line, re.IGNORECASE)
        ),
        None,
    )
    if start is None:
        return None
    end = next(
        (index for index in range(start, len(lines)) if re.match(r"^#{1,2}\s+", lines[index])),
        len(lines),
    )
    return "\n".join(lines[start:end]).strip()


def _repository_root(path: Path) -> Path:
    expanded = path.expanduser().absolute()
    try:
        status = os.lstat(expanded)
    except FileNotFoundError as exc:
        raise PreferenceCompileError("preference repository does not exist") from exc
    if not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode):
        raise PreferenceCompileError("preference repository must be a real directory")
    return expanded.resolve()


def _safe_relative_file(root: Path, relative: str) -> Path:
    requested = Path(relative)
    if requested.is_absolute() or not requested.parts or ".." in requested.parts:
        raise PreferenceCompileError("design token path escapes the repository")
    candidate = root.joinpath(requested)
    current = root
    for part in requested.parts:
        current = current / part
        try:
            status = os.lstat(current)
        except FileNotFoundError as exc:
            raise PreferenceCompileError(f"design token file is missing: {relative}") from exc
        if stat.S_ISLNK(status.st_mode):
            raise PreferenceCompileError("design token path cannot contain symlinks")
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise PreferenceCompileError("design token path escapes the repository") from exc
    return candidate


def _read_repo_file(root: Path, path: Path, maximum: int) -> bytes:
    try:
        relative = path.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise PreferenceCompileError("preference source is outside the repository") from exc
    current = root
    try:
        for part in relative.parts:
            current = current / part
            status = os.lstat(current)
            if stat.S_ISLNK(status.st_mode):
                raise PreferenceCompileError("preference source path cannot contain symlinks")
        path.resolve().relative_to(root)
    except PreferenceCompileError:
        raise
    except FileNotFoundError as exc:
        raise PreferenceCompileError("preference source does not exist") from exc
    except ValueError as exc:
        raise PreferenceCompileError("preference source escapes the repository") from exc
    if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
        raise PreferenceCompileError("preference source must be a regular non-symlink file")
    if status.st_size > maximum:
        raise PreferenceCompileError(f"preference source exceeds {maximum} bytes")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            status.st_dev,
            status.st_ino,
        ):
            raise PreferenceCompileError("preference source changed before it could be read")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError as exc:
        raise PreferenceCompileError("preference source could not be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > maximum:
        raise PreferenceCompileError(f"preference source exceeds {maximum} bytes")
    return data


def _parse_tokens(path: Path, raw: bytes) -> dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        text = raw.decode("utf-8")
        if suffix == ".json":
            value = json.loads(text)
        elif suffix == ".toml":
            value = tomllib.loads(text)
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml
                from yaml.tokens import AliasToken, AnchorToken
            except ImportError as exc:
                raise PreferenceCompileError(
                    "YAML design tokens require the preferences dependency group"
                ) from exc
            if any(isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(text)):
                raise PreferenceCompileError("YAML aliases and anchors are unsupported")
            value = yaml.safe_load(text)
        else:
            raise PreferenceCompileError(f"unsupported design token format: {suffix or 'none'}")
    except PreferenceCompileError:
        raise
    except (UnicodeDecodeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise PreferenceCompileError(f"invalid design token document: {path.name}") from exc
    if not isinstance(value, dict):
        raise PreferenceCompileError("design token document must contain an object")
    _validate_token_document(value)
    return value


def _validate_token_document(value: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        Draft202012Validator = None
    if Draft202012Validator is not None:
        errors = list(
            Draft202012Validator(
                {"type": "object", "maxProperties": 4096, "additionalProperties": True}
            ).iter_errors(value)
        )
        if errors:
            raise PreferenceCompileError("design token document violates its JSON schema")
    try:
        encoded = _canonical(value)
    except (TypeError, ValueError) as exc:
        raise PreferenceCompileError("design token values must be finite JSON data") from exc
    if len(encoded) > _MAX_SOURCE_BYTES:
        raise PreferenceCompileError("compiled design tokens exceed the source bound")
    _bounded_tree(value)


def _flatten_tokens(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, child in value.items():
        if not isinstance(key, str) or not key.strip() or "." in key:
            raise PreferenceCompileError(
                "design token keys must be non-empty and cannot contain dots"
            )
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(child, Mapping):
            flattened.update(_flatten_tokens(child, name))
        else:
            flattened[name] = child
    return flattened


def _bounded_tree(value: Any, *, depth: int = 0, count: list[int] | None = None) -> None:
    if depth > 16:
        raise PreferenceCompileError("design tokens exceed maximum nesting depth")
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > 8_192:
        raise PreferenceCompileError("design tokens contain too many values")
    if isinstance(value, Mapping):
        for child in value.values():
            _bounded_tree(child, depth=depth + 1, count=count)
    elif isinstance(value, list):
        for child in value:
            _bounded_tree(child, depth=depth + 1, count=count)


def _parse_deleted_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_RULES_PER_SOURCE:
        raise PreferenceCompileError("deleted rule IDs must be a bounded list")
    try:
        parsed = [_IDENTIFIER_ADAPTER.validate_python(item) for item in value]
    except ValidationError as exc:
        raise PreferenceCompileError("deleted rule IDs contain an invalid identifier") from exc
    if len(parsed) != len(set(parsed)):
        raise PreferenceCompileError("deleted rule IDs contain duplicates")
    return parsed


def _bounded_mapping(value: Mapping[str, Any], locator: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PreferenceCompileError(f"{locator} must be an object")
    result = dict(value)
    try:
        encoded = _canonical(result)
    except (TypeError, ValueError) as exc:
        raise PreferenceCompileError(f"{locator} must contain finite JSON data") from exc
    if len(encoded) > _MAX_SOURCE_BYTES:
        raise PreferenceCompileError(f"{locator} exceeds {_MAX_SOURCE_BYTES} bytes")
    return result


def _reject_unknown_keys(value: Mapping[str, Any], allowed: set[str], locator: str) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise PreferenceCompileError(f"{locator} contains unsupported keys: {sorted(unknown)!r}")


def _manifest(
    source_id: str,
    kind: str,
    locator: str,
    raw: bytes,
    trusted: bool,
) -> PreferenceSourceManifest:
    return PreferenceSourceManifest(
        source_id=source_id,
        kind=kind,
        locator=locator,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        trusted=trusted,
    )


def _source_order(source: RuleSource) -> int:
    return {
        RuleSource.SAFETY: 0,
        RuleSource.REPOSITORY: 1,
        RuleSource.EXPLICIT: 2,
        RuleSource.LEARNED: 3,
    }[source]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
