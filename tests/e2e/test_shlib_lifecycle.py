"""Exercise shlib with the installed CLI and a real Zsh in a disposable home."""

import stat
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_shlib_lifecycle(runner: Callable[..., str], env: dict[str, str]) -> None:
    """Keep exports, script order and installer changes through a full lifecycle."""
    home = Path(env["HOME"])

    def run(*args: str, expected: int = 0) -> str:
        return runner(*args, env=env, expected=expected)

    rc = home / ".zshrc"
    rc.write_text("typeset -g ORDER=original\n")
    rc.chmod(0o644)
    run("lion", "shlib", "install")
    root = home / ".shlib"
    token = root / "exports" / "TOKEN"
    token.write_text("literal '$HOME' $(touch SHOULD_NOT_EXIST)\nsecond line\n\n")
    token.chmod(0o600)
    (root / "exports" / "EMPTY").write_text("")
    linked = home / "linked.zsh"
    linked.write_text('ORDER+=":linked"\n')
    (root / "shlibs" / "10-linked.sh").symlink_to(linked)
    (root / "shlibs" / "20-last.sh").write_text('ORDER+=":last"\n')
    rc.write_text(rc.read_text() + 'ORDER+=":installer"\n')
    # Accept the intentional installer edit for a quiet comparison of shell behavior.
    (home / ".zshrc.lock").write_text(rc.read_text())
    probe = 'source "$HOME/.zshrc"; print -rl -- "$ORDER" "$TOKEN" "empty:${EMPTY-x}"'
    before = run("zsh", "-f", "-c", probe)
    assert before == "original:linked:last:installer\nliteral '$HOME' $(touch SHOULD_NOT_EXIST)\nsecond line\nempty:\n"
    status = run("lion", "shlib", "status", "--json")
    assert "literal" not in status
    # Syntax failures must not alter the active rc or create a partial exports file.
    broken = root / "shlibs" / "30-broken.sh"
    broken.write_text("if then\n")
    original = rc.read_bytes()
    run("lion", "shlib", "uninstall", expected=1)
    assert rc.read_bytes() == original
    assert not (home / ".zshrc.exports").exists()
    broken.unlink()
    run("lion", "shlib", "uninstall")
    assert run("zsh", "-f", "-c", probe) == before
    assert stat.S_IMODE((home / ".zshrc.exports").stat().st_mode) == 0o600
    assert stat.S_IMODE(rc.stat().st_mode) == 0o644
    assert "10-linked.sh" in rc.read_text()
    assert root.exists() and linked.exists()
    run("lion", "shlib", "uninstall")
    assert not (Path.cwd() / "SHOULD_NOT_EXIST").exists()
