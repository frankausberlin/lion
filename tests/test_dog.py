"""Tests for the watch dog: artifact rendering, enable/disable and checks."""

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from lion.classify import rules as classify_rules
from lion.cli import app
from lion.program import dog, shell
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.config import Config, ConfigError, config_path, load_config, write_config
from lion.program.dog import DogError

ORIGINAL_RC = "alias hello='echo hello'\n"

RULESET = classify_rules.load_ruleset(Path("/nonexistent/classifier.json"))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolate home/data/config and mock the Zsh validators."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(dog, "validate_zsh", Mock())
    monkeypatch.setattr(dog, "_zsh_compile_check", Mock())
    return SimpleNamespace(home=home, data=tmp_path / "data", config=tmp_path / "config")


def _ctx(home: Path) -> DoctorContext:
    return DoctorContext(state={}, home=home)


def _insert_integration(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Insert a real ~/.zshrc block with the Zsh validators mocked."""
    (home / ".zshrc").write_text(ORIGINAL_RC, encoding="utf-8")
    monkeypatch.setattr(shell, "validate_zsh", Mock())
    shell.insert(home)


def _by_name(findings: list[Finding], name: str) -> Finding:
    matches = [finding for finding in findings if finding.name == name]
    assert len(matches) == 1, [finding.name for finding in findings]
    return matches[0]


# --- transform_pattern --------------------------------------------------------


def test_transform_pattern_widens_only_a_leading_anchor() -> None:
    """A leading ``^`` gains the shared prefix class; everything else is copied."""
    assert (
        dog.transform_pattern("^(reboot|systemctl[[:space:]]+reboot)([[:space:]]|$)")
        == r"(^|[;&|[:space:]])(sudo[[:space:]]+)?(reboot|systemctl[[:space:]]+reboot)([[:space:]]|$)"
    )
    for unchanged in (
        "(^|[|;&])[[:space:]]*(sudo[[:space:]]+)?curl[^|]*[|][[:space:]]*(sudo[[:space:]]+)?(sh|bash|zsh)([[:space:]]|$)",
        "(^|[;&|[:space:]])(sudo[[:space:]]+)?rm[[:space:]]+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)([[:space:]]|$)",
    ):
        assert dog.transform_pattern(unchanged) == unchanged


# --- render_rules -------------------------------------------------------------


def test_render_rules_is_deterministic_and_covers_only_confirm_rules() -> None:
    """The artifact carries the hash, the widened patterns and the ids."""
    first = dog.render_rules(RULESET)
    second = dog.render_rules(RULESET)

    assert first == second
    lines = first.splitlines()
    assert lines[0] == f"{dog.HASH_PREFIX}{RULESET.hash}"
    assert "typeset -ga LION_DOG_PATTERNS=(" in lines
    assert "typeset -ga LION_DOG_IDS=(" in lines
    assert "  'interruptions.reboot'" in lines
    assert "  'shell.rm.recursive'" in lines
    assert "  'containers.volume.prune'" in lines
    assert "'packages.apt'" not in first
    assert r"(^|[;&|[:space:]])(sudo[[:space:]]+)?(reboot|" in first
    assert len(lines) == 1 + 1 + 7 + 1 + 1 + 7 + 1


def test_render_rules_quotes_single_quotes(tmp_path: Path) -> None:
    r"""A pattern carrying a quote is rendered with the ``'\''`` idiom."""
    user = tmp_path / "classifier.json"
    user.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "packages.quoted",
                        "group": "packages",
                        "match": "^echo 'hi'[[:space:]]+install",
                        "emit": {"manager": "x", "action": "install"},
                        "guard": "confirm",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    text = dog.render_rules(classify_rules.load_ruleset(user))

    assert r"'(^|[;&|[:space:]])(sudo[[:space:]]+)?echo '\''hi'\''[[:space:]]+install'" in text


def test_render_rules_refuses_control_characters(tmp_path: Path) -> None:
    """A pattern with control characters is refused loudly."""
    user = tmp_path / "classifier.json"
    user.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "packages.ctrl",
                        "group": "packages",
                        "match": "^apt\u001b\\[0m",
                        "emit": {"manager": "apt", "action": "install"},
                        "guard": "confirm",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ruleset = classify_rules.load_ruleset(user)

    with pytest.raises(DogError, match="control characters"):
        dog.render_rules(ruleset)


# --- zsh compile check --------------------------------------------------------


def test_zsh_compile_check_needs_zsh(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing zsh is a loud error, never a silent skip."""
    monkeypatch.setattr(dog.shutil, "which", Mock(return_value=None))
    with pytest.raises(DogError, match="zsh is required"):
        dog._zsh_compile_check("typeset -ga LION_DOG_PATTERNS=('x')\n")  # pyright: ignore[reportPrivateUsage]


def test_zsh_compile_check_detects_bad_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zsh reports a failed regcomp on stderr with status 1; that counts as failure."""
    monkeypatch.setattr(dog.shutil, "which", Mock(return_value="/usr/bin/zsh"))
    bad = SimpleNamespace(returncode=1, stdout="", stderr="failed to compile regex: Invalid regular expression")
    monkeypatch.setattr(dog.subprocess, "run", Mock(return_value=bad))

    with pytest.raises(DogError, match="failed the Zsh regex check"):
        dog._zsh_compile_check("typeset -ga LION_DOG_PATTERNS=('[')\n")  # pyright: ignore[reportPrivateUsage]


def test_zsh_compile_check_accepts_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Status 1 without diagnostics is a valid pattern that did not match empty."""
    monkeypatch.setattr(dog.shutil, "which", Mock(return_value="/usr/bin/zsh"))
    ok = SimpleNamespace(returncode=1, stdout="", stderr="")
    run = Mock(return_value=ok)
    monkeypatch.setattr(dog.subprocess, "run", run)

    dog._zsh_compile_check("typeset -ga LION_DOG_PATTERNS=('^(reboot)')\n")  # pyright: ignore[reportPrivateUsage]

    assert run.call_count == 1


# --- write_rules and read_rules_hash ------------------------------------------


def test_write_rules_writes_private_artifact(env: SimpleNamespace) -> None:
    """The artifact is written atomically with mode 600 and no leftovers."""
    path = dog.write_rules(RULESET)

    assert path == dog.rules_path()
    assert path.read_text(encoding="utf-8") == dog.render_rules(RULESET)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [child.name for child in path.parent.iterdir()] == ["rules.zsh"]
    assert dog.read_rules_hash() == RULESET.hash


def test_write_rules_failure_leaves_the_old_artifact(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed compile writes nothing and keeps a previous artifact intact."""
    path = dog.write_rules(RULESET)
    before = path.read_bytes()
    monkeypatch.setattr(dog, "validate_zsh", Mock(side_effect=ValueError("Zsh syntax validation failed")))

    with pytest.raises(ValueError, match="syntax validation failed"):
        dog.write_rules(RULESET)

    assert path.read_bytes() == before


def test_read_rules_hash_rejects_missing_and_malformed(env: SimpleNamespace) -> None:
    """Without a valid header the artifact can never count as fresh."""
    assert dog.read_rules_hash() is None
    path = dog.rules_path()
    path.parent.mkdir(parents=True)
    path.write_text("typeset -ga LION_DOG_PATTERNS=()\n", encoding="utf-8")
    assert dog.read_rules_hash() is None
    path.write_text(f"{dog.HASH_PREFIX}not-a-hash\n", encoding="utf-8")
    assert dog.read_rules_hash() is None


# --- dog_status ---------------------------------------------------------------


def test_status_defaults_without_any_file(env: SimpleNamespace) -> None:
    """A clean machine reads as off, fresh, without warnings and without writes."""
    status = dog.dog_status(env.home)

    assert status.enabled is False
    assert status.config_present is False
    assert status.confirm_rules == 7
    assert status.rules_hash == RULESET.hash
    assert status.artifact_present is False
    assert status.artifact_hash is None
    assert status.artifact_stale is False
    assert status.integration_mode == "zshrc"
    assert status.integration_inserted is False
    assert status.warnings == ()
    assert not env.data.exists()
    assert not env.config.exists()


def test_status_enabled_fresh_with_integration(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled with a fresh artifact and the integration is fully quiet."""
    _insert_integration(env.home, monkeypatch)
    dog.enable(env.home)

    status = dog.dog_status(env.home)

    assert status.enabled is True
    assert status.config_present is True
    assert status.artifact_present is True
    assert status.artifact_stale is False
    assert status.integration_inserted is True
    assert status.warnings == ()
    payload = status.to_dict()
    assert payload["enabled"] is True
    assert payload["artifact_hash"] == RULESET.hash
    assert payload["artifact_stale"] is False
    assert payload["warnings"] == []


def test_status_reports_drift(env: SimpleNamespace) -> None:
    """Missing, stale and disabled-but-present artifacts produce warnings."""
    dog.enable(env.home)
    dog.rules_path().unlink()
    assert any("artifact is missing" in warning for warning in dog.dog_status(env.home).warnings)

    dog.write_rules(RULESET)
    dog.rules_path().write_text(f"{dog.HASH_PREFIX}{'0' * 64}\n", encoding="utf-8")
    stale = dog.dog_status(env.home)
    assert stale.artifact_stale is True
    assert any("stale or unreadable" in warning for warning in stale.warnings)

    write_config(Config(watch_dog=False))
    disabled = dog.dog_status(env.home)
    assert disabled.enabled is False
    assert "disabled in the config" in disabled.warnings[0]


def test_status_integration_missing_warns_while_enabled(env: SimpleNamespace) -> None:
    """Enabled without the inserted integration points at 'lion shell insert'."""
    dog.enable(env.home)
    status = dog.dog_status(env.home)
    assert any("lion shell insert" in warning for warning in status.warnings)


def test_status_unreadable_integration_is_a_warning(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken ~/.zshrc state degrades to a warning, not a crash."""
    monkeypatch.setattr(dog.shell, "status", Mock(side_effect=ValueError("Ambiguous LION watch markers")))

    status = dog.dog_status(env.home)

    assert status.integration_mode == "unknown"
    assert status.integration_inserted is False
    assert "Cannot read the watch integration state" in status.warnings[0]


def test_status_fails_loudly_on_broken_config_and_rules(env: SimpleNamespace) -> None:
    """A broken config or classifier file fails with its path."""
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text("schema_version = 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="schema_version must be 1"):
        dog.dog_status(env.home)

    path.write_text("schema_version = 1\n", encoding="utf-8")
    user = env.config / "lion" / "classifier.json"
    user.write_text("{broken", encoding="utf-8")
    with pytest.raises(classify_rules.ClassifierRuleError, match=str(user)):
        dog.dog_status(env.home)


# --- enable / disable ---------------------------------------------------------


def test_enable_writes_config_then_artifact_and_warns_about_integration(env: SimpleNamespace) -> None:
    """Enabling writes both files, tells about new shells and warns without shell insert."""
    report = dog.enable(env.home)

    assert len(report.messages) == 3
    assert "enabled" in report.messages[0]
    assert "7 confirm rules" in report.messages[1]
    assert any("new shells" in message for message in report.messages)
    assert report.warnings == (
        "The watch integration is not inserted; run 'lion shell insert', then open a new shell.",
    )
    assert load_config().watch_dog is True
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
    assert dog.rules_path().is_file()


def test_enable_is_an_idempotent_refresh(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated 'on' recompiles the rules and reports a refresh."""
    _insert_integration(env.home, monkeypatch)
    dog.enable(env.home)

    report = dog.enable(env.home)

    assert "refreshed" in report.messages[0]
    assert report.warnings == ()


def test_disable_removes_artifact_then_flips_config(env: SimpleNamespace) -> None:
    """Disabling removes the artifact and writes the config off."""
    dog.enable(env.home)

    report = dog.disable(env.home)

    assert any("rules removed" in message for message in report.messages)
    assert any("disabled" in message for message in report.messages)
    assert not dog.rules_path().exists()
    assert load_config().watch_dog is False


def test_disable_without_files_changes_nothing(env: SimpleNamespace) -> None:
    """'off' without config and artifact reports and writes nothing."""
    report = dog.disable(env.home)

    assert report.messages == ("Guard is already off; no files changed.",)
    assert report.warnings == ()
    assert not env.data.exists()
    assert not env.config.exists()


def test_disable_cleans_up_drift_without_rewriting_config(env: SimpleNamespace) -> None:
    """A stray artifact is removed while the already-off config stays untouched."""
    dog.write_rules(RULESET)
    config_file = write_config(Config(watch_dog=False))
    before = config_file.stat().st_mtime_ns

    report = dog.disable(env.home)

    assert any("rules removed" in message for message in report.messages)
    assert not any("disabled" in message for message in report.messages)
    assert not dog.rules_path().exists()
    assert config_file.stat().st_mtime_ns == before


def test_disable_refuses_non_regular_artifact(env: SimpleNamespace) -> None:
    """A directory in the artifact's place blocks 'off' loudly."""
    dog.enable(env.home)
    dog.rules_path().unlink()
    dog.rules_path().mkdir()

    with pytest.raises(DogError, match="non-regular"):
        dog.disable(env.home)
    assert load_config().watch_dog is True


def test_enable_after_failed_artifact_write_keeps_config_intent(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The config write precedes the artifact: a compile failure leaves the guard inactive."""
    path = dog.write_rules(RULESET)
    failure = DogError("A guard pattern failed the Zsh regex check.")
    monkeypatch.setattr(dog, "_zsh_compile_check", Mock(side_effect=failure))

    with pytest.raises(DogError):
        dog.enable(env.home)

    assert load_config().watch_dog is True
    assert path.exists()


# --- dog_checks ---------------------------------------------------------------


def test_doctor_skip_when_off(env: SimpleNamespace) -> None:
    """Off without an artifact is a neutral skip."""
    finding = _by_name(dog.dog_checks(_ctx(env.home)), "watch.dog")
    assert finding.status == CheckStatus.SKIP
    assert finding.message == "Guard is off."


def test_doctor_warn_when_disabled_but_present(env: SimpleNamespace) -> None:
    """Disabled with a leftover artifact is a warn naming 'off'."""
    dog.write_rules(RULESET)

    finding = _by_name(dog.dog_checks(_ctx(env.home)), "watch.dog")

    assert finding.status == CheckStatus.WARN
    assert "disabled" in finding.message
    assert "'lion watch dog off'" in finding.hint


def test_doctor_warn_when_enabled_artifact_missing(env: SimpleNamespace) -> None:
    """Enabled without the artifact is a warn naming 'lion watch dog on'."""
    write_config(Config(watch_dog=True))

    finding = _by_name(dog.dog_checks(_ctx(env.home)), "watch.dog")

    assert finding.status == CheckStatus.WARN
    assert "missing" in finding.message
    assert "'lion watch dog on'" in finding.hint


def test_doctor_warn_when_artifact_stale(env: SimpleNamespace) -> None:
    """A hash mismatch is a warn that asks for a recompile."""
    write_config(Config(watch_dog=True))
    dog.rules_path().parent.mkdir(parents=True)
    dog.rules_path().write_text(f"{dog.HASH_PREFIX}{'0' * 64}\n", encoding="utf-8")

    finding = _by_name(dog.dog_checks(_ctx(env.home)), "watch.dog")

    assert finding.status == CheckStatus.WARN
    assert "stale" in finding.message


def test_doctor_ok_when_enabled_and_fresh(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled with a fresh artifact and the integration is ok."""
    _insert_integration(env.home, monkeypatch)
    dog.enable(env.home)

    findings = dog.dog_checks(_ctx(env.home))

    assert [finding.name for finding in findings] == ["watch.dog"]
    assert findings[0].status == CheckStatus.OK
    assert findings[0].message == "Guard armed (7 confirm rules)."


def test_doctor_warns_when_integration_missing(env: SimpleNamespace) -> None:
    """Enabled and fresh but without the integration adds the integration warn."""
    dog.enable(env.home)

    findings = dog.dog_checks(_ctx(env.home))

    assert [finding.name for finding in findings] == ["watch.dog", "watch.dog.integration"]
    assert findings[0].status == CheckStatus.OK
    assert findings[1].status == CheckStatus.WARN
    assert "'lion shell insert'" in findings[1].hint


def test_doctor_errors_on_broken_config_and_rules(env: SimpleNamespace) -> None:
    """A broken config or classifier file is an error with its path."""
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text("schema_version = 2\n", encoding="utf-8")
    finding = _by_name(dog.dog_checks(_ctx(env.home)), "watch.dog")
    assert finding.status == CheckStatus.ERROR
    assert str(path) in finding.message

    path.write_text("schema_version = 1\n", encoding="utf-8")
    user = env.config / "lion" / "classifier.json"
    user.write_text("{broken", encoding="utf-8")
    finding = _by_name(dog.dog_checks(_ctx(env.home)), "watch.dog")
    assert finding.status == CheckStatus.ERROR
    assert str(user) in finding.message


# --- command layer ------------------------------------------------------------

_cli = CliRunner()


def _invoke(*args: str):
    return _cli.invoke(app, ["watch", "dog", *args])


def test_command_bare_invocation_runs_status(env: SimpleNamespace) -> None:
    """The group without an operation shows the text status (read-only)."""
    result = _invoke()

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "enabled: False" in result.stdout
    assert "confirm_rules: 7" in result.stdout
    assert not env.data.exists()
    assert not env.config.exists()


def test_command_status_json_contract(env: SimpleNamespace) -> None:
    """The JSON status carries exactly the documented keys."""
    result = _invoke("status", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "enabled",
        "config_path",
        "config_present",
        "rules_hash",
        "confirm_rules",
        "artifact_path",
        "artifact_present",
        "artifact_hash",
        "artifact_stale",
        "integration_mode",
        "integration_inserted",
        "warnings",
    }
    assert payload["enabled"] is False
    assert payload["warnings"] == []


def test_command_on_and_off_lifecycle(env: SimpleNamespace) -> None:
    """'on' writes both files and warns about the integration; 'off' removes them."""
    started = _invoke("on")

    assert started.exit_code == 0
    assert "Guard enabled" in started.stdout
    assert "7 confirm rules" in started.stdout
    assert "lion shell insert" in started.stderr
    assert load_config().watch_dog is True
    assert dog.rules_path().is_file()

    stopped = _invoke("off")

    assert stopped.exit_code == 0
    assert "Guard disabled" in stopped.stdout
    assert not dog.rules_path().exists()
    assert load_config().watch_dog is False


def test_command_status_fails_loudly_on_broken_config(env: SimpleNamespace) -> None:
    """A broken config exits 1 and names the path on stderr."""
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text("schema_version = 2\n", encoding="utf-8")

    result = _invoke("status")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: Invalid config" in result.stderr
    assert str(path) in result.stderr
