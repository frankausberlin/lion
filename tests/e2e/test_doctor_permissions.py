"""Exercise real user permissions and preservation through the installed CLI."""

import json
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _files(root: Path) -> dict[str, tuple[bytes, int]]:
    """Capture contents and permissions of every regular file under a root."""
    return {
        str(path.relative_to(root)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in root.rglob("*")
        if path.is_file()
    }


def test_doctor_preserves_existing_state_as_user(
    user_runner: Callable[..., str], env: dict[str, str], tmp_path: Path
) -> None:
    """A non-root diagnosis preserves real history, shell files and exports."""
    home = Path(env["HOME"])
    user_runner("lion", "scan", "--json", env=env)
    user_runner("lion", "shlib", "install", env=env)
    token = home / ".shlib" / "exports" / "TOKEN"
    user_runner(
        "python3",
        "-c",
        "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_text('synthetic secret\\n'); p.chmod(0o600)",
        str(token),
        env=env,
    )
    history = tmp_path / "data" / "lion" / "history"
    before_history = _files(history)
    before_home = _files(home)
    before_data = {str(path.relative_to(tmp_path / "data")) for path in (tmp_path / "data").rglob("*")}

    stdout = user_runner("lion", "doctor", "--json", env=env)
    payload = json.loads(stdout)
    assert payload["status"] == "warn"
    assert "synthetic secret" not in stdout
    assert _files(history) == before_history
    assert _files(home) == before_home
    after_data = {str(path.relative_to(tmp_path / "data")) for path in (tmp_path / "data").rglob("*")}
    assert before_data <= after_data
    assert all(path.startswith("lion/recos") for path in after_data - before_data)
    assert len(list((tmp_path / "data" / "lion" / "recos").glob("*.sh"))) == 1
    findings = {item["name"]: item["status"] for item in payload["befunde"]}
    assert findings["shlib.installed"] == "ok"
    assert findings["storage.recos"] == "ok"


def test_unwritable_recos_reports_error_without_partial_files(
    user_runner: Callable[..., str], env: dict[str, str], tmp_path: Path
) -> None:
    """A user denied reco publication gets valid JSON and retains all files."""
    user_runner("lion", "doctor", "--json", env=env)
    recos = tmp_path / "data" / "lion" / "recos"
    before = _files(recos)
    recos.chmod(0o500)
    try:
        stdout = user_runner("lion", "doctor", "--json", env=env, expected=1)
        payload = json.loads(stdout)
        assert payload["status"] == "error"
        assert payload["reco_pfad"] is None
        findings = {item["name"]: item["status"] for item in payload["befunde"]}
        assert findings["storage.recos"] == "error"
        assert findings["storage.reco_publish"] == "error"
        assert _files(recos) == before
        assert not list(recos.glob(".reco.*"))
        assert not (tmp_path / "data" / "lion" / "history").exists()
    finally:
        recos.chmod(0o700)
