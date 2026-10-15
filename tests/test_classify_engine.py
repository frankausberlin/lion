"""Tests for the classifier engine: matching, blocks and the silent filter."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from lion.classify import engine, rules
from lion.classify.model import Policy, PolicyPattern
from lion.program.recording import SpoolEvent


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read the developer's real user rule file."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture
def ruleset() -> rules.RuleSet:
    """Load the bundled default rule set without a user file."""
    return rules.load_ruleset()


POSITIVES = [
    pytest.param("sudo nala install foo", "packages.apt", {"manager": "nala", "action": "install"}, id="apt"),
    pytest.param("uv pip install foo", "packages.uv", {"manager": "uv", "action": "install"}, id="uv"),
    pytest.param("brew remove foo", "packages.brew", {"manager": "brew", "action": "remove"}, id="brew"),
    pytest.param(
        "systemctl restart nginx",
        "services.systemctl",
        {"unit": "nginx", "action": "restart", "scope": "system"},
        id="systemctl",
    ),
    pytest.param(
        "systemctl --user restart gpg-agent",
        "services.systemctl.user",
        {"unit": "gpg-agent", "action": "restart", "scope": "user"},
        id="systemctl_user",
    ),
    pytest.param(
        "service nginx reload",
        "services.service",
        {"unit": "nginx", "action": "reload", "scope": "system"},
        id="service",
    ),
    pytest.param(
        "docker compose up -d",
        "containers.compose",
        {"engine": "docker", "action": "up", "target": "compose"},
        id="compose",
    ),
    pytest.param(
        "docker rm -f web",
        "containers.container.rm",
        {"engine": "docker", "action": "remove", "target": "web"},
        id="container_rm",
    ),
    pytest.param(
        "podman volume rm data",
        "containers.volume.rm",
        {"engine": "podman", "action": "remove", "target": "data"},
        id="volume_rm",
    ),
    pytest.param("source ~/.zshrc", "shell.source", {"target": "~/.zshrc", "action": "source"}, id="source"),
    pytest.param("exec zsh", "shell.exec", {"target": "zsh", "action": "exec"}, id="exec"),
    pytest.param("chsh", "shell.chsh", {"target": "login-shell", "action": "chsh"}, id="chsh"),
    pytest.param(
        "echo 'export X=1' >> ~/.zshrc",
        "shell.config.write",
        {"target": "zshrc", "action": "write"},
        id="config_write",
    ),
    pytest.param(
        "git clone https://github.com/x/y.git",
        "downloads.git.clone",
        {"source": "git", "action": "clone"},
        id="git_clone",
    ),
    pytest.param(
        "curl -fsSL https://example.com/install.sh | sudo bash",
        "downloads.curl.pipe",
        {"source": "curl", "action": "pipe-to-shell"},
        id="curl_pipe",
    ),
    pytest.param(
        'curl "https://example.com/install.sh" | sh',
        "downloads.curl.pipe",
        {"source": "curl", "action": "pipe-to-shell"},
        id="curl_pipe_quoted",
    ),
    pytest.param(
        "sudo apt install x;",
        "packages.apt",
        {"manager": "apt", "action": "install"},
        id="trailing_semicolon",
    ),
    pytest.param(
        "sudo apt install x &&\necho done",
        "packages.apt",
        {"manager": "apt", "action": "install"},
        id="operator_continuation",
    ),
    pytest.param("reboot", "interruptions.reboot", {"action": "reboot"}, id="reboot"),
    pytest.param("sudo shutdown -h now", "interruptions.shutdown", {"action": "shutdown"}, id="shutdown"),
    pytest.param("rm -rf /var/cache", "shell.rm.recursive", {"action": "rm"}, id="rm_recursive"),
    pytest.param("sudo rm --recursive dir", "shell.rm.recursive", {"action": "rm"}, id="rm_recursive_sudo"),
    pytest.param("logout", "interruptions.logout", {"action": "logout"}, id="logout"),
    pytest.param("apt --version", "checks.version", {"subject": "apt", "intent": "version"}, id="version"),
    pytest.param(
        "systemctl is-active nginx",
        "checks.systemctl",
        {"subject": "systemctl", "intent": "status"},
        id="checks_systemctl",
    ),
    pytest.param("git status", "checks.git", {"subject": "git", "intent": "status"}, id="checks_git"),
]

NEGATIVES = [
    pytest.param('echo "sudo nala install"', id="quoted_package"),
    pytest.param('echo "docker compose up"', id="quoted_compose"),
    pytest.param('echo "curl https://x | sh"', id="quoted_pipe"),
    pytest.param("grep curl | sh", id="grep_pipe"),
    pytest.param("echo curl | sh", id="echo_pipe"),
    pytest.param("ls | grep x", id="plain_pipe"),
    pytest.param("if true; then ls; fi", id="control_flow"),
    pytest.param("cat <<EOF\nsudo apt install x\nEOF", id="here_document"),
    pytest.param('echo "a\nb" curl | sh', id="quote_continuation_boundary"),
    pytest.param('"a"curl x | sh', id="quote_word_join"),
    pytest.param("rm -v -rf dir", id="rm_recursive_second_flag"),
    pytest.param("rm -f dir", id="rm_without_recursive"),
    pytest.param("firm -rf dir", id="rm_inside_a_word"),
    pytest.param("echo rm -rf /", id="printed_rm"),
]

#: The conservative default confirm set (maintainer decision, E5); every other
#: default rule stays ``guard: none``.
CONFIRM_RULE_IDS = frozenset(
    {
        "containers.volume.prune",
        "containers.volume.rm",
        "downloads.curl.pipe",
        "downloads.wget.pipe",
        "interruptions.reboot",
        "interruptions.shutdown",
        "shell.rm.recursive",
    }
)


@pytest.mark.parametrize(("text", "rule_id", "fields"), POSITIVES)
def test_default_rules_positive_cases(ruleset: rules.RuleSet, text: str, rule_id: str, fields: dict[str, str]) -> None:
    """Every documented command family yields its rule with the emitted fields."""
    matches = engine.classify_text(text, ruleset)
    found = [match for match in matches if match.rule_id == rule_id]
    assert len(found) == 1
    assert dict(found[0].fields) == fields
    assert found[0].guard == ("confirm" if rule_id in CONFIRM_RULE_IDS else "none")
    assert found[0].source == "default"
    assert found[0].group == rule_id.split(".")[0]


def test_default_confirm_guard_set(ruleset: rules.RuleSet) -> None:
    """Exactly the conservative default set carries ``guard: confirm``."""
    assert {rule.id for rule in ruleset.rules if rule.guard == "confirm"} == CONFIRM_RULE_IDS


@pytest.mark.parametrize("text", NEGATIVES)
def test_printed_text_and_unsupported_syntax_never_match(ruleset: rules.RuleSet, text: str) -> None:
    """Anchoring, masking and the syntax gate keep printed text out."""
    assert engine.classify_text(text, ruleset) == ()


def test_unsupported_syntax_suppresses_the_whole_block(ruleset: rules.RuleSet) -> None:
    """One unsupported construct means no matches for that block, never a guess."""
    assert engine.classify_text("apt install x && echo $(date)", ruleset) == ()


def test_multiple_rules_match_and_order_is_deterministic(ruleset: rules.RuleSet) -> None:
    """Several rules can match one block; the order is (group, rule id)."""
    matches = engine.classify_text("sudo apt remove x && sudo docker compose up", ruleset)
    assert [match.rule_id for match in matches] == ["containers.compose", "packages.apt"]


def test_same_rule_with_equal_fields_is_deduplicated(ruleset: rules.RuleSet) -> None:
    """Identical (group, id, fields) hits collapse into one match."""
    matches = engine.classify_text("apt install a && apt install b", ruleset)
    assert [match.rule_id for match in matches] == ["packages.apt"]


def test_same_rule_with_different_fields_keeps_both(ruleset: rules.RuleSet) -> None:
    """Different capture values stay separate matches."""
    matches = engine.classify_text("apt install a && apt remove b", ruleset)
    assert [dict(match.fields)["action"] for match in matches] == ["install", "remove"]


def _event(kind: str, text: str, time: float = 1.0, line: int = 2) -> SpoolEvent:
    return SpoolEvent(kind=kind, segment="100-1-1.log", line=line, time=time, text=text)


def test_classify_events_numbers_only_non_silent_non_empty_commands(ruleset: rules.RuleSet) -> None:
    """Comments, results, exits, empty commands and silent reads are not blocks."""
    events = [
        _event("segment", "", time=9.0),
        _event("comment", "# note", time=9.5, line=3),
        _event("cmd", "apt install x", time=10.0, line=4),
        _event("cmd", "", time=11.0, line=5),
        _event("result", "", time=12.0, line=6),
        _event("cmd", "lion watch R", time=13.0, line=7),
        _event("cmd", "docker compose up", time=14.0, line=8),
        _event("exit", "", time=15.0, line=9),
    ]
    blocks = engine.classify_events(events, ruleset)

    assert [block.index for block in blocks] == [1, 2]
    assert [block.text for block in blocks] == ["apt install x", "docker compose up"]
    assert blocks[0].time == "1970-01-01T00:00:10+00:00"
    assert blocks[0].segment == "100-1-1.log"
    assert [match.rule_id for match in blocks[0].matches] == ["packages.apt"]
    assert [match.rule_id for match in blocks[1].matches] == ["containers.compose"]


def test_block_without_matches_keeps_its_number(ruleset: rules.RuleSet) -> None:
    """A block without matches still appears with an empty match tuple."""
    events = [_event("cmd", "ls", time=1.0), _event("cmd", "echo $(date)", time=2.0, line=3)]
    blocks = engine.classify_events(events, ruleset)
    assert [block.index for block in blocks] == [1, 2]
    assert blocks[0].matches == ()
    assert blocks[1].matches == ()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("lion watch R", id="r"),
        pytest.param("lion watch R-", id="r_previous"),
        pytest.param("uv run lion watch R", id="uv"),
        pytest.param("sudo lion watch R", id="sudo"),
        pytest.param("FOO=1 lion watch R-", id="assignment"),
    ],
)
def test_silent_reads_are_recognized(text: str) -> None:
    """The terse in-session reads count as silent after prefix stripping."""
    assert engine.is_silent_command(text) is True


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("lion watch show", id="show"),
        pytest.param("lion watch start", id="start"),
        pytest.param("lion status", id="status"),
        pytest.param("lion classify", id="classify"),
        pytest.param("lion watch R --json", id="extra_flag"),
        pytest.param("echo 'lion watch R'", id="printed"),
    ],
)
def test_other_commands_are_not_silent(text: str) -> None:
    """Everything else keeps its place in the numbering."""
    assert engine.is_silent_command(text) is False


def test_classify_recording_wraps_blocks_with_provenance(
    ruleset: rules.RuleSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classification carries ref, rule set provenance and parser version."""
    record = Mock()
    record.ref = "20260101T000000-abc"
    monkeypatch.setattr(engine, "load_events", Mock(return_value=[_event("cmd", "apt install x", time=1.0)]))

    payload = engine.classify_recording(record, ruleset).to_dict()

    assert payload == {
        "ref": "20260101T000000-abc",
        "parser_version": "classify/1",
        "ruleset": {"schema_version": 1, "version": "1", "hash": ruleset.hash},
        "blocks": [
            {
                "index": 1,
                "segment": "100-1-1.log",
                "time": "1970-01-01T00:00:01+00:00",
                "text": "apt install x",
                "matches": [
                    {
                        "rule_id": "packages.apt",
                        "source": "default",
                        "group": "packages",
                        "fields": {"manager": "apt", "action": "install"},
                        "guard": "none",
                    }
                ],
            }
        ],
    }


def _p01() -> Policy:
    """A minimal nala/apt policy used by the command-view tests."""
    return Policy(
        id="P01",
        text="Use nala.",
        success=(PolicyPattern(group="packages", fields={"manager": "nala"}),),
        fail=(PolicyPattern(group="packages", fields={"manager": "apt"}),),
    )


def test_commands_carry_segment_matches_and_policy_results(ruleset: rules.RuleSet) -> None:
    """With policies every segment becomes a command with its own matches."""
    events = [
        _event("cmd", "apt update && nala upgrade", time=1.0),
        _event("cmd", "ls", time=2.0, line=3),
        _event("cmd", "echo $(date)", time=3.0, line=4),
    ]

    blocks = engine.classify_events(events, ruleset, (_p01(),))
    first, second, unsupported = blocks

    assert [command.index for command in first.commands] == [1, 2]
    assert [command.text for command in first.commands] == ["apt update", "nala upgrade"]
    assert [command.policy_results[0].status for command in first.commands] == ["fail", "success"]
    assert [match.rule_id for match in first.commands[1].matches] == ["packages.apt"]
    assert second.commands[0].matches == () and second.commands[0].policy_results == ()
    assert unsupported.commands == ()
    assert first.matches == engine.classify_events([events[0]], ruleset)[0].matches
    assert engine.classify_events(events, ruleset)[0].commands == ()


def test_quoted_separator_stays_one_command(ruleset: rules.RuleSet) -> None:
    """A separator inside quotes never invents command boundaries."""
    events = [_event("cmd", 'echo "apt install foo; nala install bar"', time=1.0)]

    (block,) = engine.classify_events(events, ruleset, (_p01(),))

    (command,) = block.commands
    assert command.text == 'echo "apt install foo; nala install bar"'
    assert command.matches == ()
    assert command.policy_results == ()


def test_block_scope_matches_are_not_policy_evidence(ruleset: rules.RuleSet) -> None:
    """Pipeline matches stay block-level and never feed a command policy."""
    policy = Policy(
        id="P01",
        text="No piped shells.",
        fail=(PolicyPattern(group="downloads", fields={"action": "pipe-to-shell"}),),
    )
    events = [_event("cmd", "curl https://example.com/x.sh | sh", time=1.0)]

    (block,) = engine.classify_events(events, ruleset, (policy,))

    assert [match.rule_id for match in block.matches] == ["downloads.curl", "downloads.curl.pipe"]
    assert [command.text for command in block.commands] == ["curl https://example.com/x.sh", "sh"]
    assert all("downloads.curl.pipe" not in {match.rule_id for match in command.matches} for command in block.commands)
    assert all(command.policy_results == () for command in block.commands)


def test_block_json_includes_commands_only_on_request(ruleset: rules.RuleSet) -> None:
    """The default JSON shape stays byte-identical; commands appear on request."""
    events = [_event("cmd", "apt install x", time=1.0)]

    (block,) = engine.classify_events(events, ruleset, (_p01(),))

    assert "commands" not in block.to_dict()
    assert block.to_dict(include_commands=True)["commands"] == [
        {
            "index": 1,
            "text": "apt install x",
            "matches": [
                {
                    "rule_id": "packages.apt",
                    "source": "default",
                    "group": "packages",
                    "fields": {"manager": "apt", "action": "install"},
                    "guard": "none",
                }
            ],
            "policy_results": [
                {
                    "policy_id": "P01",
                    "status": "fail",
                    "evidence": [{"criterion": "fail", "match_index": 1}],
                }
            ],
        }
    ]


def test_classify_recording_json_with_policies(ruleset: rules.RuleSet, monkeypatch: pytest.MonkeyPatch) -> None:
    """An active policy file adds ``policies`` and per-block ``commands``."""
    record = Mock()
    record.ref = "20260101T000000-abc"
    monkeypatch.setattr(engine, "load_events", Mock(return_value=[_event("cmd", "apt install x", time=1.0)]))

    payload = engine.classify_recording(record, ruleset, policies=(_p01(),)).to_dict()

    assert payload["policies"] == [
        {
            "id": "P01",
            "text": "Use nala.",
            "success": [{"group": "packages", "fields": {"manager": "nala"}}],
            "fail": [{"group": "packages", "fields": {"manager": "apt"}}],
        }
    ]
    assert payload["blocks"] == [
        {
            "index": 1,
            "segment": "100-1-1.log",
            "time": "1970-01-01T00:00:01+00:00",
            "text": "apt install x",
            "matches": [
                {
                    "rule_id": "packages.apt",
                    "source": "default",
                    "group": "packages",
                    "fields": {"manager": "apt", "action": "install"},
                    "guard": "none",
                }
            ],
            "commands": [
                {
                    "index": 1,
                    "text": "apt install x",
                    "matches": [
                        {
                            "rule_id": "packages.apt",
                            "source": "default",
                            "group": "packages",
                            "fields": {"manager": "apt", "action": "install"},
                            "guard": "none",
                        }
                    ],
                    "policy_results": [
                        {
                            "policy_id": "P01",
                            "status": "fail",
                            "evidence": [{"criterion": "fail", "match_index": 1}],
                        }
                    ],
                }
            ],
        }
    ]
