"""Declarative classifier rules: loading, validation, merging and hashing.

Rules are data, not code: one JSON file per layer describes ERE-subset patterns
with capture groups and the metadata fields they emit. The bundled
``default_rules.json`` ships with the package; an optional user file
(``$XDG_CONFIG_HOME/lion/classifier.json``) overrides rules by ``id``. Patterns
stay inside a POSIX-ERE-compatible subset (ADR 0015) so the watch dog (E5) can
compile the same rules in zsh; this module validates that subset loudly,
translates the POSIX character classes for Python's :mod:`re` at compile time,
and rejects anything it cannot translate - never guessing.

The resulting :class:`RuleSet` is a pure value: loading it is read-only and the
content hash makes the merged rule list reproducible.
"""

import hashlib
import importlib.resources
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

__all__ = [
    "GROUP_FIELDS",
    "GUARDS",
    "GUARD_CONFIRM",
    "GUARD_NONE",
    "SCHEMA_VERSION",
    "SCOPES",
    "SCOPE_BLOCK",
    "SCOPE_SEGMENT",
    "SOURCE_DEFAULT",
    "SOURCE_USER",
    "ClassifierRuleError",
    "Rule",
    "RuleSet",
    "compile_pattern",
    "default_rules_path",
    "get_config_dir",
    "load_ruleset",
    "user_rules_path",
]

#: Rule-file schema version this build understands.
SCHEMA_VERSION = 1

SCOPE_SEGMENT = "segment"
SCOPE_BLOCK = "block"
SCOPES: tuple[str, ...] = (SCOPE_SEGMENT, SCOPE_BLOCK)

GUARD_NONE = "none"
GUARD_CONFIRM = "confirm"
GUARDS: tuple[str, ...] = (GUARD_NONE, GUARD_CONFIRM)

SOURCE_DEFAULT = "default"
SOURCE_USER = "user"

#: The closed group vocabulary: every group with the fields its rules may emit.
GROUP_FIELDS: Mapping[str, tuple[str, ...]] = {
    "packages": ("manager", "action"),
    "services": ("unit", "action", "scope"),
    "containers": ("engine", "action", "target"),
    "shell": ("target", "action"),
    "downloads": ("source", "action"),
    "interruptions": ("action",),
    "checks": ("subject", "intent"),
}

#: POSIX character classes of the ERE subset, translated to Python ``re``.
_POSIX_CLASSES: Mapping[str, str] = {
    "alnum": "0-9A-Za-z",
    "alpha": "A-Za-z",
    "blank": " \t",
    "digit": "0-9",
    "lower": "a-z",
    "space": " \t\r\n\v\f",
    "upper": "A-Z",
    "xdigit": "0-9A-Fa-f",
}

_CAPTURE_REFERENCE = re.compile(r"^\$([1-9])$")
_ALLOWED_FILE_KEYS = frozenset({"schema_version", "version", "rules"})
_ALLOWED_RULE_KEYS = frozenset({"id", "group", "scope", "match", "emit", "guard"})


class ClassifierRuleError(ValueError):
    """A classifier rule file is unreadable, invalid or outside the schema."""


@dataclass(frozen=True)
class Rule:
    """One validated classification rule with its provenance."""

    id: str
    group: str
    scope: str
    match: str
    emit: Mapping[str, str]
    guard: str
    source: str

    def to_canonical(self) -> dict[str, object]:
        """Return the canonicalized rule shape used for hashing and listing."""
        return {
            "id": self.id,
            "group": self.group,
            "scope": self.scope,
            "match": self.match,
            "emit": dict(self.emit),
            "guard": self.guard,
        }


@dataclass(frozen=True)
class RuleSet:
    """The merged, validated rule set actually used for classification."""

    schema_version: int
    version: str
    rules: tuple[Rule, ...]
    hash: str
    user_path: Path
    user_loaded: bool


def get_config_dir() -> Path:
    """Return the LION configuration directory (``$XDG_CONFIG_HOME/lion``)."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".config"
    return root / "lion"


def user_rules_path() -> Path:
    """Return the user classifier file path (the file may not exist)."""
    return get_config_dir() / "classifier.json"


def default_rules_path() -> Path:
    """Return the bundled default rules file path."""
    resource = importlib.resources.files("lion.classify").joinpath("default_rules.json")
    return Path(str(resource))


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one validated ERE-subset pattern for Python's :mod:`re`."""
    return re.compile(_translate_posix_classes(pattern))


def _invalid(source: Path, message: str) -> ClassifierRuleError:
    return ClassifierRuleError(f"Invalid classifier rules: {source}: {message}")


def _escape_problem(next_char: str) -> str:
    return (
        f"escape '\\{next_char}' is not supported; use a POSIX character class "
        "(backreferences and \\b/\\d/\\s/\\w are outside the ERE subset)"
    )


def _scan_class(pattern: str, start: int) -> tuple[int, str | None]:
    """Scan one character class from its opening bracket.

    Args:
        pattern: The full pattern.
        start: Index of the opening ``[``.

    Returns:
        The index after the closing ``]`` and an error message, or ``-1`` as the
        index when the class never closes.
    """
    index = start + 1
    length = len(pattern)
    if index < length and pattern[index] == "^":
        index += 1
    if index < length and pattern[index] == "]":
        index += 1
    while index < length:
        char = pattern[index]
        if char == "\\":
            if index + 1 >= length:
                return -1, "pattern ends with a backslash"
            next_char = pattern[index + 1]
            if next_char.isascii() and next_char.isalnum():
                return -1, _escape_problem(next_char)
            index += 2
            continue
        if char == "]":
            return index + 1, None
        if char == "[" and pattern[index + 1 : index + 2] == ":":
            end = pattern.find(":]", index + 2)
            if end < 0:
                return -1, "unterminated POSIX character class"
            name = pattern[index + 2 : end]
            if name not in _POSIX_CLASSES:
                known = ", ".join(sorted(_POSIX_CLASSES))
                return -1, f"unsupported POSIX character class '[[:{name}:]]' (known: {known})"
            index = end + 2
            continue
        index += 1
    return -1, "unterminated character class"


def _scan_ere(pattern: str) -> str | None:
    """Return the reason a pattern leaves the ERE subset, or ``None``."""
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "\\":
            if index + 1 >= length:
                return "pattern ends with a backslash"
            next_char = pattern[index + 1]
            if next_char.isascii() and next_char.isalnum():
                return _escape_problem(next_char)
            index += 2
            continue
        if char == "[":
            end, problem = _scan_class(pattern, index)
            if problem is not None:
                return problem
            index = end
            continue
        if char == "(" and pattern[index + 1 : index + 2] == "?":
            return "'(?...)' group constructs are not supported"
        if char == "?" and index > 0 and pattern[index - 1] in "*+?}":
            return "non-greedy quantifiers are not supported"
        index += 1
    return None


def _translate_posix_classes(pattern: str) -> str:
    """Translate the subset's POSIX character classes for Python's :mod:`re`."""
    parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char != "[":
            parts.append(char)
            index += 1
            continue
        parts.append("[")
        index += 1
        if index < length and pattern[index] == "^":
            parts.append("^")
            index += 1
        if index < length and pattern[index] == "]":
            parts.append("]")
            index += 1
        while index < length and pattern[index] != "]":
            if pattern[index] == "\\":
                parts.append(pattern[index : index + 2])
                index += 2
                continue
            if pattern[index] == "[" and pattern[index + 1 : index + 2] == ":":
                end = pattern.find(":]", index + 2)
                parts.append(_POSIX_CLASSES[pattern[index + 2 : end]])
                index = end + 2
                continue
            parts.append(pattern[index])
            index += 1
        if index < length:
            parts.append("]")
            index += 1
    return "".join(parts)


def _validate_pattern(pattern: str, source: Path, label: str) -> re.Pattern[str]:
    """Validate the ERE subset and return the compiled Python pattern."""
    problem = _scan_ere(pattern)
    if problem is not None:
        raise _invalid(source, f"{label}: {problem}")
    try:
        return re.compile(_translate_posix_classes(pattern))
    except re.error as exc:
        raise _invalid(source, f"{label}: invalid pattern: {exc}") from exc


def _require_str(value: object, source: Path, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(source, f"{label} must be a non-empty string")
    return value


def _parse_emit(
    value: object,
    source: Path,
    label: str,
    group: str,
    groups: int,
) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise _invalid(source, f"{label}: emit must be a non-empty JSON object")
    allowed = GROUP_FIELDS[group]
    emit: dict[str, str] = {}
    for name, text in cast("dict[str, object]", value).items():
        if not isinstance(text, str) or not text:
            raise _invalid(source, f"{label}: emit['{name}'] must be a non-empty string")
        if name not in allowed:
            raise _invalid(
                source,
                f"{label}: field '{name}' is not in the '{group}' catalog ({', '.join(allowed)})",
            )
        if text.startswith("$"):
            reference = _CAPTURE_REFERENCE.match(text)
            if reference is None or int(reference.group(1)) > groups:
                raise _invalid(
                    source,
                    f"{label}: emit['{name}'] must be a literal or a $1..$9 capture of the pattern ({groups} group(s))",
                )
        emit[name] = text
    return emit


def _parse_rule(item: object, source: Path, position: int, origin: str) -> Rule:
    label = f"rule {position}"
    if not isinstance(item, dict):
        raise _invalid(source, f"{label}: must be a JSON object")
    data = cast("dict[str, object]", item)
    extra = sorted(set(data) - _ALLOWED_RULE_KEYS)
    if extra:
        raise _invalid(source, f"{label}: unknown field(s): {', '.join(extra)}")
    rule_id = _require_str(data.get("id"), source, f"{label}: id")
    group = _require_str(data.get("group"), source, f"{label}: group")
    if group not in GROUP_FIELDS:
        known = ", ".join(sorted(GROUP_FIELDS))
        raise _invalid(source, f"{label}: unknown group '{group}' (known: {known})")
    scope = data.get("scope", SCOPE_SEGMENT)
    if not isinstance(scope, str) or scope not in SCOPES:
        raise _invalid(source, f"{label}: scope must be one of {', '.join(SCOPES)}")
    match_text = _require_str(data.get("match"), source, f"{label}: match")
    guard = data.get("guard", GUARD_NONE)
    if not isinstance(guard, str) or guard not in GUARDS:
        raise _invalid(source, f"{label}: guard must be one of {', '.join(GUARDS)}")
    pattern = _validate_pattern(match_text, source, label)
    emit = _parse_emit(data.get("emit"), source, label, group, pattern.groups)
    return Rule(id=rule_id, group=group, scope=scope, match=match_text, emit=emit, guard=guard, source=origin)


def _load_document(path: Path, origin: str) -> tuple[str | None, tuple[Rule, ...]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ClassifierRuleError(f"Cannot read classifier rules: {path}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _invalid(path, str(exc)) from exc
    if not isinstance(data, dict):
        raise _invalid(path, "the top level must be a JSON object")
    document = cast("dict[str, object]", data)
    extra = sorted(set(document) - _ALLOWED_FILE_KEYS)
    if extra:
        raise _invalid(path, f"unknown top-level field(s): {', '.join(extra)}")
    schema_version = document.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        raise _invalid(path, f"schema_version must be {SCHEMA_VERSION}")
    version = document.get("version")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise _invalid(path, "version must be a non-empty string")
    rules_raw = document.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise _invalid(path, "rules must be a non-empty list")
    rules: list[Rule] = []
    seen: set[str] = set()
    for position, item in enumerate(cast("list[object]", rules_raw), start=1):
        rule = _parse_rule(item, path, position, origin)
        if rule.id in seen:
            raise _invalid(path, f"duplicate rule id '{rule.id}'")
        seen.add(rule.id)
        rules.append(rule)
    return (version if isinstance(version, str) else None), tuple(rules)


def _merge_rules(defaults: tuple[Rule, ...], overrides: tuple[Rule, ...]) -> tuple[Rule, ...]:
    """Merge the user layer over the defaults: a matching ``id`` replaces in place."""
    order = [rule.id for rule in defaults]
    merged = {rule.id: rule for rule in defaults}
    for rule in overrides:
        if rule.id not in merged:
            order.append(rule.id)
        merged[rule.id] = rule
    return tuple(merged[rule_id] for rule_id in order)


def _ruleset_hash(rules: tuple[Rule, ...]) -> str:
    """Return the content hash over the canonical merged rule list."""
    entries = [rule.to_canonical() for rule in sorted(rules, key=lambda item: item.id)]
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_ruleset(user_path: Path | None = None) -> RuleSet:
    """Load the merged rule set: bundled defaults plus the optional user file.

    A missing user file is fine and yields the defaults alone; a present but
    unreadable or invalid file - like an invalid bundled default - is a loud
    :class:`ClassifierRuleError` naming the path.

    Args:
        user_path: Override the user file location; defaults to
            :func:`user_rules_path`.

    Returns:
        The merged, validated rule set with its content hash.

    Raises:
        ClassifierRuleError: If a file is unreadable or leaves the schema,
            the ERE subset or the group vocabulary.
    """
    default_version, default_rules = _load_document(default_rules_path(), SOURCE_DEFAULT)
    path = user_rules_path() if user_path is None else user_path
    present = path.is_symlink() or path.exists()
    user_version: str | None = None
    user_rules: tuple[Rule, ...] = ()
    if present:
        user_version, user_rules = _load_document(path, SOURCE_USER)
        merged = _merge_rules(default_rules, user_rules)
    else:
        merged = default_rules
    version = user_version or default_version or str(SCHEMA_VERSION)
    return RuleSet(
        schema_version=SCHEMA_VERSION,
        version=version,
        rules=merged,
        hash=_ruleset_hash(merged),
        user_path=path,
        user_loaded=present,
    )
