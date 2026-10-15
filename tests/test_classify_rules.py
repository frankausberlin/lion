"""Tests for the classifier rule loader: schema, ERE subset, merge and hash."""

import json
from pathlib import Path

import pytest

from lion.classify import rules

USER_RULE: dict[str, object] = {
    "id": "packages.apt",
    "group": "packages",
    "scope": "segment",
    "match": "^apt[[:space:]]+install",
    "emit": {"manager": "apt", "action": "install"},
    "guard": "none",
}

CUSTOM_RULE: dict[str, object] = {
    "id": "packages.custom",
    "group": "packages",
    "match": "^custompkg[[:space:]]+install",
    "emit": {"manager": "custompkg", "action": "install"},
}


def _document(rule_items: list[dict[str, object]], version: str | None = "1") -> dict[str, object]:
    document: dict[str, object] = {"schema_version": 1, "rules": rule_items}
    if version is not None:
        document["version"] = version
    return document


def _write(tmp_path: Path, payload: object, name: str = "classifier.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


SCHEMA_CASES = [
    pytest.param([1], "top level must be a JSON object", id="top_level"),
    pytest.param({"schema_version": 2, "rules": [CUSTOM_RULE]}, "schema_version must be 1", id="schema_version"),
    pytest.param({"schema_version": True, "rules": [CUSTOM_RULE]}, "schema_version must be 1", id="schema_bool"),
    pytest.param({"schema_version": 1.0, "rules": [CUSTOM_RULE]}, "schema_version must be 1", id="schema_float"),
    pytest.param(
        _document([CUSTOM_RULE], version=""),
        "version must be a non-empty string",
        id="version_empty",
    ),
    pytest.param(
        _document([CUSTOM_RULE], version="v1\x1b[0m"),
        "version must be a non-empty string without control characters",
        id="version_controls",
    ),
    pytest.param({"schema_version": 1, "rules": []}, "rules must be a non-empty list", id="rules_empty"),
    pytest.param(
        {"schema_version": 1, "rules": [CUSTOM_RULE], "extra": 1},
        "unknown top-level field",
        id="unknown_top_key",
    ),
    pytest.param(_document([CUSTOM_RULE, dict(CUSTOM_RULE)]), "duplicate rule id", id="duplicate_id"),
    pytest.param(_document([{**CUSTOM_RULE, "group": "foo"}]), "unknown group", id="unknown_group"),
    pytest.param(_document([{**CUSTOM_RULE, "pattern": "x"}]), "unknown field", id="unknown_rule_key"),
    pytest.param(_document([{**CUSTOM_RULE, "scope": "line"}]), "scope must be one of", id="bad_scope"),
    pytest.param(_document([{**CUSTOM_RULE, "guard": "maybe"}]), "guard must be one of", id="bad_guard"),
    pytest.param(_document([{**CUSTOM_RULE, "emit": {}}]), "emit must be a non-empty JSON object", id="emit_empty"),
    pytest.param(
        _document([{**CUSTOM_RULE, "emit": {"unit": "x"}}]),
        "not in the 'packages' catalog",
        id="emit_unknown_field",
    ),
    pytest.param(
        _document([{**CUSTOM_RULE, "emit": {"manager": ""}}]),
        "must be a non-empty string",
        id="emit_empty_value",
    ),
    pytest.param(
        _document([{**CUSTOM_RULE, "emit": {"manager": "$9"}}]),
        "must be a literal or a $1..$9 capture",
        id="emit_capture_out_of_range",
    ),
    pytest.param(
        _document([{**CUSTOM_RULE, "emit": {"manager": "$x"}}]),
        "must be a literal or a $1..$9 capture",
        id="emit_capture_not_numeric",
    ),
]

PATTERN_CASES = [
    pytest.param("a\\d+", "escape '\\d' is not supported", id="escape_class"),
    pytest.param("\\bword\\b", "escape '\\b' is not supported", id="escape_word"),
    pytest.param("(a)\\1", "escape '\\1' is not supported", id="backreference"),
    pytest.param("a\\", "pattern ends with a backslash", id="trailing_backslash"),
    pytest.param("(?:a)", "'(?...)' group constructs are not supported", id="group_construct"),
    pytest.param("a(?=b)", "'(?...)' group constructs are not supported", id="lookahead"),
    pytest.param("a*?b", "non-greedy quantifiers are not supported", id="non_greedy"),
    pytest.param("a+?", "non-greedy quantifiers are not supported", id="non_greedy_plus"),
    pytest.param("[[:weird:]]", "unsupported POSIX character class", id="unknown_posix"),
    pytest.param("[abc", "unterminated character class", id="unterminated_class"),
    pytest.param("[[:space]", "unterminated POSIX character class", id="unterminated_posix"),
    pytest.param("a(b", "invalid pattern", id="invalid"),
    pytest.param("(a+)+$", "nested quantifiers are not supported", id="nested_repetition"),
    pytest.param("(a*)*", "nested quantifiers are not supported", id="nested_star"),
    pytest.param("(a?)*", "nested quantifiers are not supported", id="nested_optional"),
    pytest.param("([ab]+)+", "nested quantifiers are not supported", id="nested_class_repetition"),
    pytest.param("(a{2,})+", "nested quantifiers are not supported", id="nested_brace"),
    pytest.param("(a|a)*", "must start with disjoint characters", id="overlapping_alternation"),
    pytest.param("(a|ab)+", "must start with disjoint characters", id="prefix_alternation"),
    pytest.param("(x(a|a)y)*", "must start with disjoint characters", id="nested_overlapping_alternation"),
]


def test_defaults_load_with_a_deterministic_hash(tmp_path: Path) -> None:
    """A missing user file yields the bundled defaults with a stable hash."""
    first = rules.load_ruleset(tmp_path / "missing.json")
    second = rules.load_ruleset(tmp_path / "missing.json")

    assert first.rules
    assert first.version == "1"
    assert first.schema_version == 1
    assert first.user_loaded is False
    assert first.hash == second.hash
    assert {rule.source for rule in first.rules} == {rules.SOURCE_DEFAULT}


def test_user_file_replaces_by_id_and_appends_new_rules(tmp_path: Path) -> None:
    """A user rule with a known id replaces it in place; new ids are appended."""
    path = _write(tmp_path, _document([USER_RULE, CUSTOM_RULE], version="2"))

    merged = rules.load_ruleset(path)

    replaced = next(rule for rule in merged.rules if rule.id == "packages.apt")
    assert replaced.source == rules.SOURCE_USER
    assert replaced.match == USER_RULE["match"]
    assert merged.version == "2"
    assert merged.user_loaded is True
    assert merged.user_path == path
    user_ids = [rule.id for rule in merged.rules if rule.source == rules.SOURCE_USER]
    assert user_ids == ["packages.apt", "packages.custom"]
    assert merged.hash != rules.load_ruleset(tmp_path / "missing.json").hash


def test_user_file_without_version_keeps_the_default_version(tmp_path: Path) -> None:
    """An omitted user version falls back to the default rule set version."""
    path = _write(tmp_path, _document([CUSTOM_RULE], version=None))
    assert rules.load_ruleset(path).version == "1"


def test_rule_order_does_not_change_the_hash(tmp_path: Path) -> None:
    """The hash is canonical: the file order of the rules is irrelevant."""
    first = _write(tmp_path, _document([USER_RULE, CUSTOM_RULE]), name="a.json")
    second = _write(tmp_path, _document([CUSTOM_RULE, USER_RULE]), name="b.json")
    assert rules.load_ruleset(first).hash == rules.load_ruleset(second).hash


@pytest.mark.parametrize(("payload", "message"), SCHEMA_CASES)
def test_invalid_documents_fail_loudly(tmp_path: Path, payload: object, message: str) -> None:
    """Schema violations raise ``ClassifierRuleError`` naming the file."""
    path = _write(tmp_path, payload)
    with pytest.raises(rules.ClassifierRuleError) as excinfo:
        rules.load_ruleset(path)
    assert message in str(excinfo.value)
    assert str(path) in str(excinfo.value)


@pytest.mark.parametrize(("pattern", "message"), PATTERN_CASES)
def test_patterns_outside_the_ere_subset_fail_loudly(tmp_path: Path, pattern: str, message: str) -> None:
    """Forbidden constructs are rejected instead of compiled hopefully."""
    path = _write(tmp_path, _document([{**CUSTOM_RULE, "match": pattern}]))
    with pytest.raises(rules.ClassifierRuleError) as excinfo:
        rules.load_ruleset(path)
    assert message in str(excinfo.value)


def test_unreadable_and_unparsable_files_fail_loudly(tmp_path: Path) -> None:
    """A broken JSON document and a directory both fail with the path."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(rules.ClassifierRuleError) as excinfo:
        rules.load_ruleset(broken)
    assert "Invalid classifier rules" in str(excinfo.value)

    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(rules.ClassifierRuleError) as excinfo:
        rules.load_ruleset(directory)
    assert "Cannot read classifier rules" in str(excinfo.value)
    assert str(directory) in str(excinfo.value)


def test_complexity_checks_allow_disjoint_or_bounded_repetitions(tmp_path: Path) -> None:
    """Safe shapes stay accepted: disjoint alternations, single optional groups."""
    rules_items = [
        {**CUSTOM_RULE, "match": "^(a|b)+[[:space:]]+c$"},
        {**CUSTOM_RULE, "id": "packages.flags", "match": "^tool([[:space:]]+-[^[:space:]]+)?[[:space:]]+run"},
        {**CUSTOM_RULE, "id": "packages.optional", "match": "^tool([[:space:]]+pip)?[[:space:]]+run"},
    ]
    path = _write(tmp_path, _document(rules_items))
    ruleset = rules.load_ruleset(path)
    assert {rule.id for rule in ruleset.rules if rule.source == rules.SOURCE_USER} == {
        "packages.custom",
        "packages.flags",
        "packages.optional",
    }


def test_compile_pattern_translates_posix_classes() -> None:
    """The compiled python pattern honors the POSIX classes of the subset."""
    compiled = rules.compile_pattern("^a[[:space:]]+b[[:digit:]]$")
    assert compiled.match("a b") is None
    assert compiled.match("a \tb1") is not None


def test_default_rules_cover_every_group(tmp_path: Path) -> None:
    """The bundled default rule set uses every documented group."""
    groups = {rule.group for rule in rules.load_ruleset(tmp_path / "missing.json").rules}
    assert groups == set(rules.GROUP_FIELDS)


def test_default_segment_rules_are_anchored(tmp_path: Path) -> None:
    """Segment-scope defaults anchor at the start after the prefix strip.

    ``shell.rm.recursive`` carries the shared raw-buffer prefix class
    ``(^|[;&|[:space:]])`` instead of a bare ``^``, so the same string also
    matches ``; rm -rf …`` and ``sudo rm -rf …`` when the watch dog searches
    the raw ZLE buffer (ADR 0020).
    """
    for rule in rules.load_ruleset(tmp_path / "missing.json").rules:
        if rule.scope == rules.SCOPE_SEGMENT:
            assert rule.match.startswith("^") or rule.match.startswith("(^|[;&|[:space:]])")
