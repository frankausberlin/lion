"""Tests for the policy loader: TOML schema, validation and evaluation."""

from pathlib import Path

import pytest

from lion.classify import policies
from lion.classify.model import Match, Policy, PolicyEvidence, PolicyPattern

BASE_DOCUMENT = """
schema_version = 1

[[policies]]
id = "P01"
text = "Install and remove Debian packages with nala."
success = [{ group = "packages", fields = { action = ["install", "remove"], manager = "nala" } }]
fail = [{ group = "packages", fields = { action = ["install", "remove"], manager = ["apt", "apt-get"] } }]

[[policies]]
id = "P02"
text = "Use uv for global Python packages."
"""


def _write(tmp_path: Path, text: str, name: str = "policies.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _document(policy_body: str) -> str:
    return "schema_version = 1\n\n[[policies]]\n" + policy_body


def _match(group: str, **fields: str) -> Match:
    return Match(rule_id=f"{group}.test", source="default", group=group, fields=fields, guard="none")


def test_missing_file_is_inactive(tmp_path: Path) -> None:
    """A missing policy file means no policies and no evaluation."""
    assert policies.load_policies(tmp_path / "missing.toml") == ()


def test_empty_policy_list_is_inactive(tmp_path: Path) -> None:
    """A present file with an empty list activates nothing."""
    path = _write(tmp_path, "schema_version = 1\npolicies = []\n")
    assert policies.load_policies(path) == ()


def test_load_policies_uses_the_user_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an override the loader reads ``$XDG_CONFIG_HOME/lion/policies.toml``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    path = tmp_path / "cfg" / "lion" / "policies.toml"
    path.parent.mkdir(parents=True)
    path.write_text(BASE_DOCUMENT, encoding="utf-8")

    assert [policy.id for policy in policies.load_policies()] == ["P01", "P02"]


def test_valid_file_loads_definitions_and_criteria(tmp_path: Path) -> None:
    """Definitions keep their order, texts and typed patterns."""
    first, second = policies.load_policies(_write(tmp_path, BASE_DOCUMENT))

    assert first.id == "P01"
    assert first.text == "Install and remove Debian packages with nala."
    assert second.id == "P02"
    assert second.success == () and second.fail == ()
    assert first.success[0].group == "packages"
    assert first.success[0].fields == {"action": ("install", "remove"), "manager": "nala"}
    assert first.fail[0].fields["manager"] == ("apt", "apt-get")


def test_multiline_text_and_group_only_patterns_are_valid(tmp_path: Path) -> None:
    """A multiline rule text and a pattern without fields stay accepted."""
    path = _write(
        tmp_path,
        """
schema_version = 1

[[policies]]
id = "P03"
text = \"\"\"
Steer services in the user scope,
never system-wide.
\"\"\"
fail = [{ group = "interruptions" }]
""",
    )

    (policy,) = policies.load_policies(path)

    assert "user scope" in policy.text
    assert policy.fail[0].fields == {}


def test_unreadable_path_fails_loudly(tmp_path: Path) -> None:
    """A directory instead of a file is an error naming the path."""
    directory = tmp_path / "dir"
    directory.mkdir()

    with pytest.raises(policies.ClassifierPolicyError) as excinfo:
        policies.load_policies(directory)

    assert "Cannot read policies" in str(excinfo.value)
    assert str(directory) in str(excinfo.value)


INVALID_DOCUMENTS = [
    pytest.param("{not toml", "Invalid policies", id="broken_toml"),
    pytest.param("schema_version = 1\npolicies = []\nextra = 1\n", "unknown top-level field", id="unknown_top_key"),
    pytest.param("schema_version = 2\npolicies = []\n", "schema_version must be 1", id="schema_version"),
    pytest.param("schema_version = true\npolicies = []\n", "schema_version must be 1", id="schema_bool"),
    pytest.param("schema_version = 1.0\npolicies = []\n", "schema_version must be 1", id="schema_float"),
    pytest.param("policies = []\n", "schema_version must be 1", id="schema_missing"),
    pytest.param("schema_version = 1\n", "policies must be a list of tables", id="policies_missing"),
    pytest.param('schema_version = 1\npolicies = "x"\n', "policies must be a list of tables", id="policies_not_list"),
    pytest.param("schema_version = 1\npolicies = [1]\n", "must be a TOML table", id="policy_not_table"),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nextra = 1\n'),
        "unknown field(s): extra",
        id="unknown_policy_key",
    ),
    pytest.param(_document('text = "T"\n'), "id must be a non-empty string", id="id_missing"),
    pytest.param(_document('id = "  "\ntext = "T"\n'), "id must be a non-empty string", id="id_blank"),
    pytest.param(
        _document('id = "P\\u001b"\ntext = "T"\n'),
        "id must not contain control characters",
        id="id_controls",
    ),
    pytest.param(_document('id = "P01"\n'), "text must be a non-empty string", id="text_missing"),
    pytest.param(
        _document('id = "P01"\ntext = "T\\u0007"\n'),
        "text must not contain control characters other than newline and tab",
        id="text_controls",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = []\n'),
        "success must be a non-empty list of patterns",
        id="success_empty",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = { group = "packages" }\n'),
        "success must be a non-empty list of patterns",
        id="success_not_list",
    ),
    pytest.param(_document('id = "P01"\ntext = "T"\nsuccess = [1]\n'), "must be a TOML table", id="pattern_not_table"),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "packages", extra = 1 }]\n'),
        "unknown field(s): extra",
        id="pattern_unknown_key",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "nope" }]\n'),
        "unknown group 'nope'",
        id="pattern_unknown_group",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "packages", fields = "x" }]\n'),
        "fields must be a table",
        id="fields_not_table",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "packages", fields = { unit = "x" } }]\n'),
        "not in the 'packages' catalog",
        id="field_unknown",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "packages", fields = { manager = "" } }]\n'),
        "must be a non-empty string or a non-empty list",
        id="field_empty_value",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "packages", fields = { manager = [] } }]\n'),
        "must be a non-empty string or a non-empty list",
        id="field_empty_list",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "packages", fields = { manager = ["apt", 3] } }]\n'),
        "must be a non-empty string or a non-empty list",
        id="field_bad_list_item",
    ),
    pytest.param(
        _document('id = "P01"\ntext = "T"\nsuccess = [{ group = "packages", fields = { manager = 3 } }]\n'),
        "must be a non-empty string or a non-empty list",
        id="field_bad_type",
    ),
]


@pytest.mark.parametrize(("text", "message"), INVALID_DOCUMENTS)
def test_invalid_documents_fail_loudly(tmp_path: Path, text: str, message: str) -> None:
    """Schema violations raise ``ClassifierPolicyError`` naming the file."""
    path = _write(tmp_path, text)

    with pytest.raises(policies.ClassifierPolicyError) as excinfo:
        policies.load_policies(path)

    assert message in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_duplicate_ids_fail_loudly(tmp_path: Path) -> None:
    """Two policies with the same id are rejected."""
    text = _document('id = "P01"\ntext = "A"\n') + '\n[[policies]]\nid = "P01"\ntext = "B"\n'

    with pytest.raises(policies.ClassifierPolicyError) as excinfo:
        policies.load_policies(_write(tmp_path, text))

    assert "duplicate policy id 'P01'" in str(excinfo.value)


def test_user_policy_path_follows_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path is the policy file inside the LION config directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    assert policies.user_policies_path() == tmp_path / "cfg" / "lion" / "policies.toml"


def test_pattern_matching_semantics() -> None:
    """Equality, membership, missing fields and the group-only pattern."""
    pattern = PolicyPattern(group="packages", fields={"action": ("install", "remove"), "manager": "nala"})

    assert policies.pattern_matches(pattern, _match("packages", manager="nala", action="install")) is True
    assert policies.pattern_matches(pattern, _match("packages", manager="nala", action="update")) is False
    assert policies.pattern_matches(pattern, _match("packages", manager="apt", action="install")) is False
    assert policies.pattern_matches(pattern, _match("services", manager="nala", action="install")) is False
    assert policies.pattern_matches(pattern, _match("packages", manager="nala")) is False

    group_only = PolicyPattern(group="packages", fields={})
    assert policies.pattern_matches(group_only, _match("packages", action="anything")) is True
    assert policies.pattern_matches(group_only, _match("services", action="anything")) is False


def test_evaluate_command_statuses_and_evidence() -> None:
    """Success, fail, conflict and no-hit follow the documented rules."""
    nala = PolicyPattern(group="packages", fields={"manager": "nala"})
    apt = PolicyPattern(group="packages", fields={"manager": "apt"})
    policy = Policy(id="P01", text="rule text", success=(nala,), fail=(apt,))

    success = policies.evaluate_command((policy,), (_match("packages", manager="nala", action="install"),))
    assert [result.status for result in success] == ["success"]
    assert success[0].evidence == (PolicyEvidence(criterion="success", match_index=1),)

    fail = policies.evaluate_command((policy,), (_match("packages", manager="apt", action="install"),))
    assert [result.status for result in fail] == ["fail"]
    assert fail[0].evidence == (PolicyEvidence(criterion="fail", match_index=1),)

    conflict = policies.evaluate_command(
        (policy,),
        (_match("packages", manager="nala"), _match("packages", manager="apt")),
    )
    assert [result.status for result in conflict] == ["conflict"]
    assert conflict[0].evidence == (
        PolicyEvidence(criterion="success", match_index=1),
        PolicyEvidence(criterion="fail", match_index=2),
    )

    assert policies.evaluate_command((policy,), (_match("packages", manager="brew"),)) == ()
    assert policies.evaluate_command((policy,), ()) == ()


def test_policies_without_or_with_a_single_criterion() -> None:
    """A policy without criteria and a single-criterion policy stay precise."""
    nala = PolicyPattern(group="packages", fields={"manager": "nala"})
    apt = PolicyPattern(group="packages", fields={"manager": "apt"})
    instruction = Policy(id="P02", text="Working instruction.")
    only_success = Policy(id="P03", text="Nala.", success=(nala,))
    only_fail = Policy(id="P04", text="No apt.", fail=(apt,))

    assert policies.evaluate_command((instruction,), (_match("packages", manager="apt"),)) == ()
    assert policies.evaluate_command((only_success,), (_match("packages", manager="apt"),)) == ()
    assert policies.evaluate_command((only_fail,), (_match("packages", manager="nala"),)) == ()
    success_statuses = [
        result.status for result in policies.evaluate_command((only_success,), (_match("packages", manager="nala"),))
    ]
    fail_statuses = [
        result.status for result in policies.evaluate_command((only_fail,), (_match("packages", manager="apt"),))
    ]
    assert success_statuses == ["success"]
    assert fail_statuses == ["fail"]
