"""Declarative command policies: loading, validation and read-time evaluation.

Policies are data, not code: one optional TOML file describes human-readable
work rules and, per rule, the command shapes that count as compliant
(``success``) or as a violation (``fail``). A criterion is a list of patterns
over the closed classifier group/field catalog: the list means OR (also across
groups), the fields inside one pattern mean AND, and a list value means OR
over the values. A missing field never matches; there is no negation, no
expression language and no evaluation of anything beyond equality checks.

The file lives at ``$XDG_CONFIG_HOME/lion/policies.toml``. A missing file or
an empty policy list activates nothing and leaves every output unchanged; a
present but unreadable or invalid file fails loudly with its path. Loading and
evaluating is read-only: no command is executed, nothing is persisted and the
exit code never depends on a policy outcome.
"""

import tomllib
from pathlib import Path
from typing import cast

from lion.classify.model import Match, Policy, PolicyEvidence, PolicyPattern, PolicyResult
from lion.classify.rules import GROUP_FIELDS, get_config_dir
from lion.classify.text import has_controls, sanitize_controls

__all__ = [
    "SCHEMA_VERSION",
    "ClassifierPolicyError",
    "evaluate_command",
    "load_policies",
    "pattern_matches",
    "user_policies_path",
]

#: Policy-file schema version this build understands.
SCHEMA_VERSION = 1

_ALLOWED_FILE_KEYS = frozenset({"schema_version", "policies"})
_ALLOWED_POLICY_KEYS = frozenset({"id", "text", "success", "fail"})
_ALLOWED_PATTERN_KEYS = frozenset({"group", "fields"})


class ClassifierPolicyError(ValueError):
    """A policy file is unreadable, invalid or outside the schema."""


def user_policies_path() -> Path:
    """Return the user policy file path (the file may not exist)."""
    return get_config_dir() / "policies.toml"


def _invalid(source: Path, message: str) -> ClassifierPolicyError:
    clean_source = sanitize_controls(str(source))
    return ClassifierPolicyError(f"Invalid policies: {clean_source}: {sanitize_controls(message)}")


def _has_forbidden_controls(text: str) -> bool:
    """Return whether ``text`` carries controls beyond newline and tab."""
    return has_controls(text.replace("\n", " ").replace("\t", " "))


def _require_str(value: object, source: Path, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(source, f"{label} must be a non-empty string")
    return value


def _parse_field_value(value: object, source: Path, label: str) -> str | tuple[str, ...]:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        items = cast("list[object]", value)
        if items and all(isinstance(item, str) and item for item in items):
            return tuple(cast("list[str]", items))
    raise _invalid(source, f"{label} must be a non-empty string or a non-empty list of them")


def _parse_pattern(item: object, source: Path, label: str) -> PolicyPattern:
    if not isinstance(item, dict):
        raise _invalid(source, f"{label}: must be a TOML table")
    data = cast("dict[str, object]", item)
    extra = sorted(set(data) - _ALLOWED_PATTERN_KEYS)
    if extra:
        raise _invalid(source, f"{label}: unknown field(s): {', '.join(extra)}")
    group = _require_str(data.get("group"), source, f"{label}: group")
    if group not in GROUP_FIELDS:
        known = ", ".join(sorted(GROUP_FIELDS))
        raise _invalid(source, f"{label}: unknown group '{group}' (known: {known})")
    allowed = GROUP_FIELDS[group]
    fields_raw = data.get("fields", {})
    if not isinstance(fields_raw, dict):
        raise _invalid(source, f"{label}: fields must be a table")
    fields: dict[str, str | tuple[str, ...]] = {}
    for name, value in cast("dict[str, object]", fields_raw).items():
        if name not in allowed:
            raise _invalid(source, f"{label}: field '{name}' is not in the '{group}' catalog ({', '.join(allowed)})")
        fields[name] = _parse_field_value(value, source, f"{label}: fields['{name}']")
    return PolicyPattern(group=group, fields=fields)


def _parse_criterion(value: object, source: Path, label: str) -> tuple[PolicyPattern, ...]:
    if not isinstance(value, list) or not value:
        raise _invalid(source, f"{label} must be a non-empty list of patterns")
    return tuple(
        _parse_pattern(item, source, f"{label}[{position}]")
        for position, item in enumerate(cast("list[object]", value), start=1)
    )


def _parse_policy(item: object, source: Path, position: int) -> Policy:
    label = f"policy {position}"
    if not isinstance(item, dict):
        raise _invalid(source, f"{label}: must be a TOML table")
    data = cast("dict[str, object]", item)
    extra = sorted(set(data) - _ALLOWED_POLICY_KEYS)
    if extra:
        raise _invalid(source, f"{label}: unknown field(s): {', '.join(extra)}")
    policy_id = _require_str(data.get("id"), source, f"{label}: id")
    if has_controls(policy_id):
        raise _invalid(source, f"{label}: id must not contain control characters")
    text = _require_str(data.get("text"), source, f"{label}: text")
    if _has_forbidden_controls(text):
        raise _invalid(source, f"{label}: text must not contain control characters other than newline and tab")
    success = _parse_criterion(data["success"], source, f"{label}: success") if "success" in data else ()
    fail = _parse_criterion(data["fail"], source, f"{label}: fail") if "fail" in data else ()
    return Policy(id=policy_id, text=text, success=success, fail=fail)


def load_policies(path: Path | None = None) -> tuple[Policy, ...]:
    """Load the optional user policy file (read-only).

    A missing file or an empty policy list returns no policies: nothing is
    evaluated and every output stays unchanged. A present but unreadable or
    invalid file is a loud :class:`ClassifierPolicyError` naming the path.

    Args:
        path: Override the user file location; defaults to
            :func:`user_policies_path`.

    Returns:
        The loaded policies in file order.

    Raises:
        ClassifierPolicyError: If the file is unreadable or leaves the schema.
    """
    resolved = user_policies_path() if path is None else path
    if not (resolved.is_symlink() or resolved.exists()):
        return ()
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ClassifierPolicyError(
            f"Cannot read policies: {sanitize_controls(str(resolved))}: {sanitize_controls(str(exc))}"
        ) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _invalid(resolved, str(exc)) from exc
    extra = sorted(set(data) - _ALLOWED_FILE_KEYS)
    if extra:
        raise _invalid(resolved, f"unknown top-level field(s): {', '.join(extra)}")
    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        raise _invalid(resolved, f"schema_version must be {SCHEMA_VERSION}")
    policies_raw = data.get("policies")
    if not isinstance(policies_raw, list):
        raise _invalid(resolved, "policies must be a list of tables")
    policies: list[Policy] = []
    seen: set[str] = set()
    for position, item in enumerate(cast("list[object]", policies_raw), start=1):
        policy = _parse_policy(item, resolved, position)
        if policy.id in seen:
            raise _invalid(resolved, f"duplicate policy id '{policy.id}'")
        seen.add(policy.id)
        policies.append(policy)
    return tuple(policies)


def pattern_matches(pattern: PolicyPattern, match: Match) -> bool:
    """Return whether one classifier match satisfies one policy pattern."""
    if pattern.group != match.group:
        return False
    for name, expected in pattern.fields.items():
        actual = match.fields.get(name)
        if actual is None:
            return False
        if isinstance(expected, tuple):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _criterion_hits(patterns: tuple[PolicyPattern, ...], matches: tuple[Match, ...]) -> tuple[int, ...]:
    """Return the 1-based indices of the matches satisfying any pattern."""
    return tuple(
        index
        for index, match in enumerate(matches, start=1)
        if any(pattern_matches(pattern, match) for pattern in patterns)
    )


def evaluate_command(policies: tuple[Policy, ...], matches: tuple[Match, ...]) -> tuple[PolicyResult, ...]:
    """Evaluate every policy against the matches of one command (read-only).

    A policy without a hit yields no result; a hit is ``success``, ``fail`` or
    ``conflict`` when both criteria match, and the contributing match indices
    are kept as evidence in criterion order.
    """
    results: list[PolicyResult] = []
    for policy in policies:
        success = _criterion_hits(policy.success, matches)
        fail = _criterion_hits(policy.fail, matches)
        if not success and not fail:
            continue
        status = "conflict" if success and fail else "success" if success else "fail"
        evidence = tuple(
            PolicyEvidence(criterion=criterion, match_index=index)
            for criterion, hits in (("success", success), ("fail", fail))
            for index in hits
        )
        results.append(PolicyResult(policy_id=policy.id, status=status, evidence=evidence))
    return tuple(results)
