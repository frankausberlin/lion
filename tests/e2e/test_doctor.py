"""End-to-end tests for ``lion doctor``."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def _lion(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the installed lion entry point with ``HOME`` pointed at ``home``."""
    executable = shutil.which("lion")
    assert executable is not None, "lion entry point is not on PATH"
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    return subprocess.run(
        [executable, *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_home(root: Path) -> Path:
    """Create a throwaway home with an original ``.zshrc``."""
    root.mkdir(parents=True, exist_ok=True)
    home = root / "home"
    home.mkdir()
    (home / ".zshrc").write_text("export ORIGINAL=1\n", encoding="utf-8")
    return home


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def test_doctor_install_dryrun_fix_roundtrip(tmp_path: Path) -> None:
    """Doctor reports, leaves state alone on dry-run and applies only scoped fixes."""
    home = _make_home(tmp_path)
    installed = _lion(home, "shlib", "install")
    assert installed.returncode == 0, installed.stderr

    shlib = home / ".shlib"
    key = shlib / ".redaction-key"
    exports = shlib / "exports"
    orphan = shlib / "locks" / "orphan.lock"
    assert key.is_file()
    assert _mode(key) == 0o600

    checked = _lion(home, "doctor")
    assert checked.returncode == 1
    assert "shlib.cleanup_original" in checked.stdout
    assert "no suspected secrets found (best effort)" in checked.stdout
    assert key.is_file()

    shutil.rmtree(exports)
    key.unlink()
    orphan.write_text("orphan\n", encoding="utf-8")

    broken = _lion(home, "doctor")
    assert broken.returncode == 1
    assert "shlib.missing_dir" in broken.stdout
    assert "redaction.key_missing" in broken.stdout
    assert "locks.orphan" in broken.stdout
    assert not exports.exists()
    assert not key.exists()
    assert orphan.is_file()

    dry = _lion(home, "doctor", "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "Dry run: no changes made." in dry.stdout
    assert not exports.exists()
    assert not key.exists()
    assert orphan.is_file()

    fixed = _lion(home, "doctor", "--fix")
    assert fixed.returncode == 1
    assert exports.is_dir()
    assert _mode(exports) == 0o700
    assert _mode(key) == 0o600
    assert not orphan.exists()

    (shlib / "shlibs" / "00-original-zshrc.sh").unlink()
    clean = _lion(home, "doctor")
    assert clean.returncode == 0, clean.stderr
    assert "no suspected secrets found (best effort)" in clean.stdout


def test_doctor_secret_value_is_never_printed(tmp_path: Path) -> None:
    """A suspected token is reported as metadata only, never as a raw value."""
    home = _make_home(tmp_path)
    installed = _lion(home, "shlib", "install")
    assert installed.returncode == 0, installed.stderr

    token = "ghp_" + "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY4"
    assert len(token) - len("ghp_") == 36
    export = home / ".shlib" / "exports" / "TOKEN"
    export.write_text(token + "\n", encoding="utf-8")
    export.chmod(0o644)

    result = _lion(home, "doctor")
    output = result.stdout + result.stderr

    assert result.returncode == 1
    assert "possible github-token" in output
    assert "confidence 95%" in output
    assert "mode 0644" in output
    assert "rule provider.github_token" in output
    assert token not in output


def test_doctor_exit_codes_for_broken_layout(tmp_path: Path) -> None:
    """A missing install exits 1 and an unusable layout exits 2."""
    missing = _make_home(tmp_path / "missing")
    absent = _lion(missing, "doctor")
    assert absent.returncode == 1
    assert "shlib.not_installed" in absent.stdout

    inconsistent = _make_home(tmp_path / "inconsistent")
    real = tmp_path / "real"
    real.mkdir()
    (inconsistent / ".shlib").symlink_to(real)

    refused = _lion(inconsistent, "doctor")
    assert refused.returncode == 2
    assert "shlib.inconsistent" in refused.stdout
