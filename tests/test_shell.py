"""Tests for the watch integration in the shell configuration."""

import json
import stat
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from lion.program import recording, shell, shlib
from lion.program.checks import CheckStatus, DoctorContext
from lion.program.shell import ShellError

ORIGINAL_RC = "alias hello='echo hello'\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a disposable home with a mocked syntax checker."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(shell, "validate_zsh", Mock())
    monkeypatch.setattr(shlib, "_validate", Mock())
    return tmp_path


def _rc(home: Path) -> Path:
    return home / ".zshrc"


def _install_shlib(home: Path) -> None:
    _rc(home).write_text(ORIGINAL_RC)
    shlib.install(home)


def _ctx(home: Path) -> DoctorContext:
    return DoctorContext(state={}, home=home)


def _warnings(result: dict[str, object]) -> list[str]:
    warnings = result["warnings"]
    assert isinstance(warnings, list)
    return [str(item) for item in cast("list[object]", warnings)]


def test_insert_zshrc_mode(home: Path) -> None:
    """Without shlib the integration becomes a marked block at the end."""
    _rc(home).write_text(ORIGINAL_RC)
    _rc(home).chmod(0o644)

    report = shell.insert(home)

    text = _rc(home).read_text()
    assert any("inserted" in message for message in report.messages)
    assert text.startswith(ORIGINAL_RC)
    assert shell.BLOCK_START in text and shell.BLOCK_END in text
    assert shell.MARKER in text
    assert text.count(shell.BLOCK_START) == 1
    assert stat.S_IMODE(_rc(home).stat().st_mode) == 0o644
    assert (home / ".zshrc.before-lion-watch").read_text() == ORIGINAL_RC
    assert not shell.script_path(home).exists()

    result = shell.status(home)
    assert result["mode"] == "zshrc"
    assert result["inserted"] is True
    assert result["modified"] is False
    assert result["path"] == str(_rc(home))
    assert _warnings(result) == []


def test_insert_zshrc_without_existing_rc(home: Path) -> None:
    """A missing ~/.zshrc is created with the block only."""
    report = shell.insert(home)

    assert any("inserted" in message for message in report.messages)
    text = _rc(home).read_text()
    assert text.startswith(shell.BLOCK_START)
    assert not (home / ".zshrc.before-lion-watch").exists()


def test_insert_zshrc_is_idempotent(home: Path) -> None:
    """Inserting the identical block again changes nothing."""
    shell.insert(home)
    before = _rc(home).read_bytes()

    report = shell.insert(home)

    assert report.messages == ("Watch integration is already inserted; no files changed.",)
    assert _rc(home).read_bytes() == before


def test_insert_zshrc_refuses_modified_block(home: Path) -> None:
    """A block that differs from the delivered integration is a loud error."""
    shell.insert(home)
    text = _rc(home).read_text().replace("LION_WATCH_CAP:-4096", "LION_WATCH_CAP:-99")
    _rc(home).write_text(text)
    before = _rc(home).read_bytes()

    with pytest.raises(ShellError, match="differs from the delivered integration"):
        shell.insert(home)
    assert _rc(home).read_bytes() == before
    assert shell.status(home)["modified"] is True


def test_ambiguous_markers_are_loud(home: Path) -> None:
    """Duplicate or crossed markers stop insert, remove and status."""
    shell.insert(home)
    text = _rc(home).read_text()
    _rc(home).write_text(text + text)

    with pytest.raises(ShellError, match="Ambiguous LION watch markers"):
        shell.insert(home)
    with pytest.raises(ShellError, match="Ambiguous LION watch markers"):
        shell.remove(home)
    with pytest.raises(ShellError, match="Ambiguous LION watch markers"):
        shell.status(home)


def test_remove_zshrc_restores_original(home: Path) -> None:
    """Removing the block restores the previous ~/.zshrc byte for byte."""
    _rc(home).write_text(ORIGINAL_RC)
    shell.insert(home)

    report = shell.remove(home)

    assert any("removed" in message for message in report.messages)
    assert _rc(home).read_text() == ORIGINAL_RC
    assert (home / ".zshrc.before-lion-watch-remove").read_text() != ORIGINAL_RC
    assert shell.status(home)["inserted"] is False
    assert shell.remove(home).messages == ("Watch integration is not inserted; no files changed.",)


def test_insert_refuses_symlinked_zshrc(home: Path) -> None:
    """A symlinked ~/.zshrc is never replaced by insert."""
    target = home / "real-rc"
    target.write_text(ORIGINAL_RC)
    _rc(home).symlink_to(target)

    with pytest.raises(ShellError, match="Refusing to replace non-regular file"):
        shell.insert(home)

    assert _rc(home).is_symlink()
    assert target.read_text() == ORIGINAL_RC
    assert not (home / ".zshrc.before-lion-watch").exists()


def test_remove_refuses_symlinked_zshrc(home: Path) -> None:
    """A symlinked ~/.zshrc with a block is never replaced by remove."""
    _rc(home).write_text(ORIGINAL_RC)
    shell.insert(home)
    block = _rc(home).read_text()
    target = home / "real-rc"
    target.write_text(block)
    _rc(home).unlink()
    _rc(home).symlink_to(target)

    with pytest.raises(ShellError, match="Refusing to replace non-regular file"):
        shell.remove(home)

    assert _rc(home).is_symlink()
    assert _rc(home).resolve() == target
    assert target.read_text() == block
    assert not (home / ".zshrc.before-lion-watch-remove").exists()


def test_shlib_mode_lifecycle(home: Path) -> None:
    """With shlib installed the integration is a numbered script."""
    _install_shlib(home)
    rc_before = _rc(home).read_text()

    report = shell.insert(home)

    target = shell.script_path(home)
    assert any(str(target) in message for message in report.messages)
    assert target.is_file()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == shell.INTEGRATION
    assert _rc(home).read_text() == rc_before
    result = shell.status(home)
    assert result["mode"] == "shlib"
    assert result["inserted"] is True and result["modified"] is False

    assert shell.insert(home).messages == ("Watch integration is already inserted; no files changed.",)
    report = shell.remove(home)
    assert not target.exists()
    assert shell.status(home)["inserted"] is False


def test_shlib_mode_requires_shlibs_dir(home: Path) -> None:
    """A shlib installation without shlibs/ is refused without changes."""
    _install_shlib(home)
    for entry in (home / ".shlib" / "shlibs").iterdir():
        entry.unlink()
    (home / ".shlib" / "shlibs").rmdir()

    with pytest.raises(ShellError, match="shlibs is missing"):
        shell.insert(home)
    assert not shell.script_path(home).exists()


def test_shlib_mode_rejects_zshrc_block(home: Path) -> None:
    """A leftover block while shlib is active is ambiguous for insert."""
    _install_shlib(home)
    _rc(home).write_text(_rc(home).read_text() + shell.block_text())

    with pytest.raises(ShellError, match="while shlib is installed"):
        shell.insert(home)


def test_zshrc_mode_rejects_script_leftover(home: Path) -> None:
    """A leftover shlib script blocks the zshrc-mode insert."""
    target = shell.script_path(home)
    target.parent.mkdir(parents=True)
    target.write_text(shell.INTEGRATION)

    with pytest.raises(ShellError, match="requires reconciliation"):
        shell.insert(home)
    assert _warnings(shell.status(home))


def test_remove_refuses_foreign_script(home: Path) -> None:
    """A file without the marker line requires manual reconciliation."""
    target = shell.script_path(home)
    target.parent.mkdir(parents=True)
    target.write_text("echo foreign\n")

    with pytest.raises(ShellError, match="Foreign file without the LION watch marker"):
        shell.remove(home)
    assert target.exists()


def test_remove_refuses_two_artifacts(home: Path) -> None:
    """Both artifacts at once are ambiguous."""
    shell.insert(home)
    target = shell.script_path(home)
    target.parent.mkdir(parents=True)
    target.write_text(shell.INTEGRATION)

    with pytest.raises(ShellError, match="Two watch artifacts present"):
        shell.remove(home)


def test_remove_warns_while_recording(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing during an active recording warns but proceeds."""
    shell.insert(home)
    recording.start_recording({"host": {"status": "ok", "error": "", "hostname": "lion"}})

    report = shell.remove(home)

    assert any("recording is active" in warning for warning in report.warnings)
    assert _rc(home).read_text() == ""


def test_status_warns_when_shlib_script_is_missing(home: Path) -> None:
    """Shlib installed without the watch script warns with the path."""
    _install_shlib(home)

    result = shell.status(home)

    assert result["mode"] == "shlib"
    assert result["inserted"] is False
    warnings = _warnings(result)
    assert any("watch script is missing" in warning for warning in warnings)
    assert any(str(shell.script_path(home)) in warning for warning in warnings)


def test_status_warns_while_recording_without_integration(home: Path) -> None:
    """An active recording without the integration is flagged."""
    recording.start_recording({"host": {"status": "ok", "error": "", "hostname": "lion"}})

    result = shell.status(home)

    assert result["inserted"] is False
    assert any("no events are captured" in warning for warning in _warnings(result))


def test_status_is_read_only(home: Path) -> None:
    """Status never creates or changes a file."""
    _rc(home).write_text(ORIGINAL_RC)
    before = sorted((path.name, path.read_bytes()) for path in home.iterdir() if path.is_file())

    result = shell.status(home)

    assert result["inserted"] is False
    after = sorted((path.name, path.read_bytes()) for path in home.iterdir() if path.is_file())
    assert after == before


def test_zdotdir_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A foreign ZDOTDIR is refused before any shell configuration is touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZDOTDIR", str(tmp_path / "elsewhere"))

    with pytest.raises(ValueError, match="ZDOTDIR"):
        shell.get_home()


def test_artifact_contains_hooks_and_segment() -> None:
    """The delivered artifact carries the marker, hooks, masker and p10k segment."""
    text = shell.INTEGRATION
    assert text.splitlines()[0] == shell.MARKER
    for needle in (
        "add-zsh-hook preexec _lion_watch_preexec",
        "add-zsh-hook precmd _lion_watch_precmd",
        "add-zsh-hook zshaddhistory _lion_watch_addhistory",
        "add-zsh-hook zshexit _lion_watch_zshexit",
        "LION_WATCH_MASK",
        "prompt_lion_watch",
        "POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS",
        "read -r line < /proc/$$/stat",
        '"segment"$' + "'\\t'",
        "lines=(${(f)input})",
    ):
        assert needle in text
    segment_start = text.index("function prompt_lion_watch()")
    segment_body = text[segment_start : text.index("_lion_watch_register_p10k() {")]
    assert "LION_WATCH_POINTER" in segment_body
    assert "/proc/" not in segment_body


def test_artifact_contains_the_dog_guard() -> None:
    """The delivered artifact carries the interactive-only dog loader and widget."""
    text = shell.INTEGRATION
    for needle in (
        'LION_DOG_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/lion/dog/rules.zsh"',
        'if [[ -o interactive && -r "$LION_DOG_FILE" ]]; then',
        'source "$LION_DOG_FILE" 2>/dev/null',
        "(( ${#LION_DOG_PATTERNS} ))",
        "_lion_dog_accept() {",
        "setopt local_options no_ksharrays",
        "[[ $buf =~ $pat ]]",
        '[[ $buf == "$LION_DOG_ARMED" ]]',
        "_lion_dog_clear_armed() {",
        "add-zle-hook-widget line-pre-redraw _lion_dog_clear_armed",
        "zle .accept-line",
        "zle -N accept-line _lion_dog_accept",
        "zle -A accept-line lion-dog-accept",
        'zle -M "LION dog: ${LION_DOG_IDS[idx]}',
    ):
        assert needle in text
    dog_block = text[text.index("typeset -g LION_DOG_FILE=") :]
    assert "python" not in dog_block.lower()
    assert dog_block.rstrip().endswith("true")


def test_block_contains_integration() -> None:
    """The inlined block wraps the artifact in markers on their own lines."""
    block = shell.block_text()
    assert block.startswith(shell.BLOCK_START + "\n")
    assert block.endswith(shell.BLOCK_END + "\n")
    assert shell.INTEGRATION in block


def test_shell_checks_report_states(home: Path) -> None:
    """Doctor findings flip from skip to ok and warn with the installation."""
    findings = shell.shell_checks(_ctx(home))
    assert [finding.status for finding in findings] == [CheckStatus.SKIP]

    shell.insert(home)
    findings = shell.shell_checks(_ctx(home))
    assert any(finding.name == "shell.inserted" and finding.status == CheckStatus.OK for finding in findings)

    text = _rc(home).read_text().replace("LION_WATCH_CAP:-4096", "LION_WATCH_CAP:-99")
    _rc(home).write_text(text)
    findings = shell.shell_checks(_ctx(home))
    assert any(finding.name == "shell.modified" and finding.status == CheckStatus.WARN for finding in findings)


def test_shell_checks_tolerate_ambiguous_markers(home: Path) -> None:
    """Ambiguous markers become an error finding instead of aborting doctor."""
    shell.insert(home)
    text = _rc(home).read_text()
    _rc(home).write_text(text + text)

    findings = shell.shell_checks(_ctx(home))

    assert any(finding.name == "shell.status" and finding.status == CheckStatus.ERROR for finding in findings)


def test_status_json_shape(home: Path) -> None:
    """The status mapping is JSON-serializable with the documented keys."""
    shell.insert(home)

    payload = json.loads(json.dumps(shell.status(home)))

    assert set(payload) == {"mode", "path", "inserted", "modified", "warnings"}
