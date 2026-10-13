"""Deterministic tests for ``lion doctor`` and its check framework.

The collectors are replaced by a fixed state and ``HOME``/``XDG_DATA_HOME`` point
at temporary directories, so no real hardware, packages or user shell is
touched.
"""

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lion.classify.checks import classifier_checks
from lion.cli import app
from lion.command import doctor as command_doctor
from lion.program import annotations, doctor, recording, storage, wiki
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.shlib import BLOCK
from lion.program.storage import get_data_dir, get_history_dir, get_recos_dir
from lion.state import diagnosis

runner = CliRunner()

T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)

_NVIDIA_HARDWARE: dict[str, object] = {
    "status": "ok",
    "error": "",
    "gpu_vendor": "nvidia",
    "compute_platform": "cuda",
    "gpu": [{"vendor": "nvidia", "driver": "nvidia"}],
}


@pytest.fixture(autouse=True)
def doctor_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolate the home and data directories from the developer's machine."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    return SimpleNamespace(home=home, data=data)


def _state(**overrides: object) -> dict[str, dict[str, object]]:
    """Return a healthy collector state with optional section overrides."""
    state: dict[str, dict[str, object]] = {
        "host": {
            "status": "ok",
            "error": "",
            "hostname": "lion",
            "distribution": "Ubuntu",
            "distribution_version": "24.04",
        },
        "hardware": {"status": "ok", "error": "", "gpu_vendor": "none", "compute_platform": "none", "gpu": []},
        "network": {"status": "ok", "error": "", "interfaces": []},
        "packages": {"status": "ok", "error": ""},
        "services": {"status": "ok", "error": "", "units": []},
        "containers": {
            "status": "ok",
            "error": "",
            "runtime": "none",
            "server_version": "",
            "storage_driver": "",
            "rootless": False,
            "images": [],
            "volumes": [],
            "networks": [],
            "containers": [],
        },
        "tools": {
            "status": "ok",
            "error": "",
            "available": {
                "lspci": True,
                "nvidia_smi": False,
                "rocm_smi": False,
                "apt_mark": True,
                "systemctl": True,
                "docker": False,
                "podman": False,
                "zsh": True,
            },
        },
        "dash": {"status": "ok", "error": "", "files": []},
    }
    for name, section in overrides.items():
        state[name] = section  # type: ignore[assignment]
    return state


def _use_state(monkeypatch: pytest.MonkeyPatch, state: dict[str, dict[str, object]]) -> None:
    def fake_collect(_collectors: object) -> dict[str, dict[str, object]]:
        return state

    monkeypatch.setattr(command_doctor, "collect_state", fake_collect)


def _invoke(*args: str):
    return runner.invoke(app, ["doctor", *args])


def _reco_files() -> list[Path]:
    recos = get_recos_dir()
    return sorted(recos.glob("*.sh")) if recos.exists() else []


def _freeze(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return instant

    monkeypatch.setattr(storage, "datetime", FrozenDatetime)


def test_all_ok_writes_nothing(monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace) -> None:
    """A clean machine reports ok/skip, exits 0 and writes no artifact."""
    _use_state(monkeypatch, _state())

    result = _invoke()

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "Result: 15 ok, 0 warn, 0 error, 10 skip" in result.stdout
    assert "Reco script" not in result.stdout
    assert not get_data_dir().exists()
    assert _reco_files() == []


def test_warn_writes_single_reco(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing required tool is a warn that publishes exactly one script."""
    _use_state(
        monkeypatch,
        _state(hardware=_NVIDIA_HARDWARE),
    )

    result = _invoke()

    assert result.exit_code == 0
    assert "[warning] tools.nvidia_smi" in result.stdout
    scripts = _reco_files()
    assert len(scripts) == 1
    assert str(scripts[0]) in result.stdout
    assert stat.S_IMODE(scripts[0].stat().st_mode) == 0o700
    assert stat.S_IMODE(get_recos_dir().stat().st_mode) == 0o700
    content = scripts[0].read_text()
    assert "nvidia-smi" in content
    assert "NVIDIA driver" in content
    assert "never executes this script" in content


def test_show_prints_reco_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--show`` prints the generated script only in text mode."""
    _use_state(
        monkeypatch,
        _state(hardware=_NVIDIA_HARDWARE),
    )

    result = _invoke("--show")

    assert result.exit_code == 0
    assert "#!/usr/bin/env bash" in result.stdout
    assert "READ COMPLETELY BEFORE RUNNING" in result.stdout


def test_json_is_pure_and_schema_shaped(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--json`` emits one JSON object and never leaks the reco content."""
    _use_state(
        monkeypatch,
        _state(hardware=_NVIDIA_HARDWARE),
    )

    result = _invoke("--json")

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "warn"
    assert payload["checked"] == [
        "collectors",
        "tools",
        "history",
        "storage",
        "wiki",
        "shlib",
        "shell",
        "watch",
        "classify",
    ]
    assert payload["summary"]["warn"] == 1
    assert payload["reco_path"] is not None
    assert payload["reco_path"].endswith(".sh")
    nvidia = next(item for item in payload["findings"] if item["name"] == "tools.nvidia_smi")
    assert nvidia["status"] == "warn"
    assert nvidia["commands"] == []


def test_damaged_history_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged history entry is an error naming its path and exits 1."""
    _use_state(monkeypatch, _state())
    history = get_history_dir()
    history.mkdir(parents=True)
    (history / "broken.toml").write_text("broken = [")

    result = _invoke()

    assert result.exit_code == 1
    assert "[error] history.broken.toml" in result.stdout
    assert "broken.toml" in result.stdout


def test_unwritable_data_dir_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-writable data directory is an error and exits 1."""
    _use_state(monkeypatch, _state())

    def deny_access(_path: object, _mode: int) -> bool:
        return False

    monkeypatch.setattr(storage.os, "access", deny_access)

    result = _invoke()

    assert result.exit_code == 1
    assert "storage.data_dir" in result.stdout
    assert "is not writable" in result.stdout


def test_reco_publish_failure_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed reco publication is reported as an error instead of crashing."""
    _use_state(
        monkeypatch,
        _state(hardware=_NVIDIA_HARDWARE),
    )

    def fail(content: str) -> Path:
        raise OSError("read-only")

    monkeypatch.setattr(command_doctor, "publish_reco", fail)

    result = _invoke()

    assert result.exit_code == 1
    assert "storage.reco_publish" in result.stdout
    assert "could not be written" in result.stdout


def test_foreign_zdotdir_without_install_is_skip(monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace) -> None:
    """A foreign ZDOTDIR without a managed install is a neutral skip, not an error."""
    _use_state(monkeypatch, _state())
    monkeypatch.setenv("ZDOTDIR", str(doctor_env.home / "other"))

    result = _invoke("--json")

    assert result.exit_code == 0
    findings = {item["name"]: item for item in json.loads(result.stdout)["findings"]}
    assert findings["shlib.home"]["status"] == "skip"


def test_foreign_zdotdir_with_install_is_error(monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace) -> None:
    """A foreign ZDOTDIR with a managed install stays an error."""
    _use_state(monkeypatch, _state())
    home = doctor_env.home
    (home / ".zshrc").write_text(BLOCK)
    (home / ".zshrc.lock").write_text(BLOCK)
    (home / ".shlib" / "exports").mkdir(parents=True)
    (home / ".shlib" / "shlibs").mkdir(parents=True)
    monkeypatch.setenv("ZDOTDIR", str(home / "other"))

    result = _invoke()

    assert result.exit_code == 1
    assert "[error] shlib.home" in result.stdout


def test_skip_for_optional_and_foreign_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-Debian and optional tools are neutral skips, not warnings."""
    _use_state(
        monkeypatch,
        _state(
            host={
                "status": "ok",
                "error": "",
                "hostname": "arch",
                "distribution": "Arch Linux",
                "distribution_version": "rolling",
            },
            tools={
                "status": "ok",
                "error": "",
                "available": {
                    "lspci": True,
                    "nvidia_smi": False,
                    "rocm_smi": True,
                    "apt_mark": True,
                    "systemctl": True,
                    "zsh": False,
                },
            },
        ),
    )

    result = _invoke("--json")

    assert result.exit_code == 0
    findings = {item["name"]: item["status"] for item in json.loads(result.stdout)["findings"]}
    assert findings["tools.apt_mark"] == "skip"
    assert findings["tools.nvidia_smi"] == "skip"
    assert findings["tools.rocm_smi"] == "skip"
    assert findings["tools.zsh"] == "skip"
    assert _reco_files() == []


def test_read_only_preserves_history_and_recos(monkeypatch: pytest.MonkeyPatch) -> None:
    """A warn run creates recos/ but never touches history/."""
    _use_state(
        monkeypatch,
        _state(hardware=_NVIDIA_HARDWARE),
    )

    _ = _invoke()

    assert len(_reco_files()) == 1
    assert not get_history_dir().exists()


def test_reco_collision_never_overwrites(monkeypatch: pytest.MonkeyPatch) -> None:
    """A same-instant second run gets a ``~NNNN`` suffix and keeps the first."""
    _freeze(monkeypatch, T0)

    first = storage.publish_reco("#!/usr/bin/env bash\n")
    second = storage.publish_reco("#!/usr/bin/env bash\n")

    assert first.name == "2026-10-09T12-00-00.000000Z.sh"
    assert second.name.endswith("~0001.sh")
    assert first.read_text() == "#!/usr/bin/env bash\n"


def test_reco_never_follows_symlink(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing symlink at the target name is skipped, not written through."""
    _freeze(monkeypatch, T0)
    recos = get_recos_dir()
    recos.mkdir(parents=True)
    victim = recos / "victim"
    victim.write_text("original")
    (recos / "2026-10-09T12-00-00.000000Z.sh").symlink_to(victim)

    published = storage.publish_reco("# new\n")

    assert published.name.endswith("~0001.sh")
    assert victim.read_text() == "original"


def test_run_checks_isolates_crashing_check(monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace) -> None:
    """A check that raises becomes an error finding and does not abort the run."""

    def boom(ctx: DoctorContext) -> list[Finding]:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(doctor, "CHECKS", (("collectors", boom),))

    findings = doctor.run_checks(DoctorContext(state=_state(), home=doctor_env.home))

    assert len(findings) == 1
    assert findings[0].status == CheckStatus.ERROR
    assert findings[0].topic == "collectors"
    assert "kaputt" in findings[0].message


def test_overall_and_summary_are_skip_neutral() -> None:
    """``skip`` never escalates the overall status."""
    findings = [
        Finding(topic="history", name="history.entries", status=CheckStatus.SKIP, message="leer"),
        Finding(topic="collectors", name="collectors.host", status=CheckStatus.OK, message="ok"),
    ]

    assert doctor.overall(findings) == CheckStatus.OK
    assert doctor.summary(findings) == {"ok": 1, "warn": 0, "error": 0, "skip": 1}


def test_help_documents_doctor() -> None:
    """``lion doctor --help`` explains the read-only diagnosis and the reco."""
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "recos" in result.stdout
    assert "read-only diagnosis" in result.stdout


def test_collector_checks_flag_missing_and_incomplete(doctor_env: SimpleNamespace) -> None:
    """A missing section and an incomplete collector are both warnings."""
    state = _state(
        hardware={
            "status": "unavailable",
            "error": "MemTotal missing",
            "gpu_vendor": "none",
            "compute_platform": "none",
        }
    )
    del state["packages"]

    findings = diagnosis.collector_checks(DoctorContext(state=state, home=doctor_env.home))
    by_name = {finding.name: finding for finding in findings}

    assert by_name["collectors.packages"].status == CheckStatus.WARN
    assert "missing" in by_name["collectors.packages"].message
    assert by_name["collectors.hardware"].status == CheckStatus.WARN
    assert "MemTotal missing" in by_name["collectors.hardware"].message


def test_missing_lspci_on_debian_offers_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ``lspci`` on Debian yields a safe, distro-aware apt command."""
    _use_state(
        monkeypatch,
        _state(tools={"status": "ok", "error": "", "available": {"lspci": False, "apt_mark": True}}),
    )

    result = _invoke("--show")

    assert result.exit_code == 0
    assert "[warning] tools.lspci" in result.stdout
    assert "sudo apt install pciutils" in result.stdout


def test_missing_lspci_elsewhere_has_no_command(doctor_env: SimpleNamespace) -> None:
    """A missing ``lspci`` outside Debian stays a comment-only hint."""
    state = _state(
        host={
            "status": "ok",
            "error": "",
            "hostname": "arch",
            "distribution": "Arch Linux",
            "distribution_version": "rolling",
        },
        tools={"status": "ok", "error": "", "available": {"lspci": False, "apt_mark": True}},
    )

    findings = {
        finding.name: finding for finding in diagnosis.tool_checks(DoctorContext(state=state, home=doctor_env.home))
    }

    assert findings["tools.lspci"].status == CheckStatus.WARN
    assert findings["tools.lspci"].commands == ()


def test_missing_apt_mark_on_debian_has_no_command(doctor_env: SimpleNamespace) -> None:
    """A missing ``apt-mark`` on Debian is a warning without a safe command."""
    state = _state(tools={"status": "ok", "error": "", "available": {"lspci": True, "apt_mark": False}})

    findings = {
        finding.name: finding for finding in diagnosis.tool_checks(DoctorContext(state=state, home=doctor_env.home))
    }

    assert findings["tools.apt_mark"].status == CheckStatus.WARN
    assert findings["tools.apt_mark"].commands == ()


def test_tool_policy_helpers() -> None:
    """The private policy helpers cover the unknown and zsh cases."""
    requirement = diagnosis._requirement  # pyright: ignore[reportPrivateUsage]
    remediation = diagnosis._remediation  # pyright: ignore[reportPrivateUsage]
    has_nvidia = diagnosis._has_nvidia_gpu  # pyright: ignore[reportPrivateUsage]

    assert requirement("unknown", False, "") == (False, "unknown tool")
    assert requirement("nvidia_smi", True, "Ubuntu")[0] is True
    assert requirement("nvidia_smi", False, "Ubuntu")[0] is False
    assert remediation("nvidia_smi", "Ubuntu")[0] == ()
    assert remediation("apt_mark", "Ubuntu")[0] == ()
    assert remediation("unknown", "Ubuntu") == ((), "")

    assert has_nvidia({"gpu": [{"vendor": "amd"}, {"vendor": "nvidia"}]}) is True
    assert has_nvidia({"gpu": [{"vendor": "amd", "driver": "amdgpu"}]}) is False
    assert has_nvidia({"gpu": [{"driver": "nvidia"}]}) is True
    assert has_nvidia({"gpu": []}) is False
    assert has_nvidia({"compute_platform": "cuda"}) is True
    assert has_nvidia({"gpu_vendor": "none"}) is False


def test_nvidia_smi_required_for_mixed_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mixed AMD+NVIDIA machine still requires nvidia-smi, per-GPU evidence."""
    _use_state(
        monkeypatch,
        _state(
            hardware={
                "status": "ok",
                "error": "",
                "gpu_vendor": "mixed",
                "compute_platform": "mixed",
                "gpu": [
                    {"vendor": "amd", "driver": "amdgpu"},
                    {"vendor": "nvidia", "driver": "nvidia"},
                ],
            },
            tools={
                "status": "ok",
                "error": "",
                "available": {"lspci": True, "nvidia_smi": False, "apt_mark": True},
            },
        ),
    )

    result = _invoke("--json")

    findings = {item["name"]: item["status"] for item in json.loads(result.stdout)["findings"]}
    assert findings["tools.nvidia_smi"] == "warn"


def test_nvidia_smi_not_required_for_amd_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AMD-only machine never requires nvidia-smi, even with rocm drivers."""
    _use_state(
        monkeypatch,
        _state(
            hardware={
                "status": "ok",
                "error": "",
                "gpu_vendor": "amd",
                "compute_platform": "rocm",
                "gpu": [{"vendor": "amd", "driver": "amdgpu"}],
            },
            tools={
                "status": "ok",
                "error": "",
                "available": {"lspci": True, "nvidia_smi": True, "apt_mark": True},
            },
        ),
    )

    result = _invoke("--json")

    findings = {item["name"]: item["status"] for item in json.loads(result.stdout)["findings"]}
    assert findings["tools.nvidia_smi"] == "skip"


def test_shlib_install_without_reference_warns(monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace) -> None:
    """A managed installation without ~/.zshrc.lock is a warning, not ok."""
    _use_state(monkeypatch, _state())
    home = doctor_env.home
    (home / ".zshrc").write_text(BLOCK)
    (home / ".shlib" / "exports").mkdir(parents=True)
    (home / ".shlib" / "shlibs").mkdir(parents=True)

    result = _invoke("--json")

    findings = {item["name"]: item for item in json.loads(result.stdout)["findings"]}
    assert findings["shlib.installed"]["status"] == "ok"
    assert findings["shlib.lock"]["status"] == "warn"
    assert "missing" in findings["shlib.lock"]["message"]


def test_dash_defects_checked_without_install(monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace) -> None:
    """Dash entries are inspected even when the managed block is absent."""
    _use_state(monkeypatch, _state())
    home = doctor_env.home
    dash = home / ".shlib" / "dash"
    dash.mkdir(parents=True)
    real = home / "real.conf"
    real.write_text("x")
    (dash / "ok.conf").symlink_to(real)
    (dash / "broken.conf").symlink_to(home / "missing")
    (dash / "plain.conf").write_text("plain")

    result = _invoke("--json")

    findings = {item["name"]: item for item in json.loads(result.stdout)["findings"]}
    assert findings["shlib.installed"]["status"] == "skip"
    assert findings["shlib.dash.broken.conf"]["status"] == "warn"
    assert findings["shlib.dash.plain.conf"]["status"] == "warn"
    assert "shlib.dash.ok.conf" not in findings


def test_reco_sanitizes_finding_name_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    """A filename with a newline cannot inject an executable line into the reco."""
    _use_state(monkeypatch, _state())
    history = get_history_dir()
    history.mkdir(parents=True)
    (history / "evil\nwhoami\n#x.toml").write_text("broken = [")

    result = _invoke()

    assert result.exit_code == 1
    scripts = _reco_files()
    assert len(scripts) == 1
    allowed = {"", "#!/usr/bin/env bash", "set -euo pipefail"}
    for line in scripts[0].read_text().splitlines():
        assert line in allowed or line.startswith("#"), f"unexpected executable line: {line!r}"


def test_shell_and_watch_topics_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean env reports the shell and watch topics as neutral skips."""
    _use_state(monkeypatch, _state())

    result = _invoke("--json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["checked"] == [
        "collectors",
        "tools",
        "history",
        "storage",
        "wiki",
        "shlib",
        "shell",
        "watch",
        "classify",
    ]
    findings = {item["name"]: item for item in payload["findings"]}
    assert findings["shell.inserted"]["status"] == "skip"
    assert findings["watch.records"]["status"] == "skip"
    assert payload["status"] == "ok"


def test_damaged_recording_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged recording is an error naming its path and publishes a reco."""
    _use_state(monkeypatch, _state())
    record_dir = recording.get_recordings_dir() / "2026-10-05T20-00-00.000000Z"
    record_dir.mkdir(parents=True)
    (record_dir / "meta.toml").write_text("broken = [")

    result = _invoke()

    assert result.exit_code == 1
    assert "watch.record" in result.stdout
    assert str(record_dir) in result.stdout
    assert len(_reco_files()) == 1


def test_active_recording_without_integration_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """An active recording without the shell integration warns once."""
    _use_state(monkeypatch, _state())
    recording.start_recording({"host": {"status": "ok", "error": "", "hostname": "lion"}})

    result = _invoke("--json")

    assert result.exit_code == 0
    findings = {item["name"]: item for item in json.loads(result.stdout)["findings"]}
    assert findings["watch.active"]["status"] == "ok"
    assert findings["shell.warning"]["status"] == "warn"
    assert "no events are captured" in findings["shell.warning"]["message"]


def test_annotations_check_reports_invalid_overlay(doctor_env: SimpleNamespace) -> None:
    """An invalid adr.toml is an error finding; absent and valid ones are not."""
    state = _state()
    record = recording.start_recording(state, "")
    _ = recording.stop_recording(state)
    ctx = DoctorContext(state=state, home=doctor_env.home)

    assert annotations.annotations_checks(ctx) == []

    path = record.path / annotations.OVERLAY_FILENAME
    path.write_text("broken = [")
    findings = annotations.annotations_checks(ctx)

    assert len(findings) == 1
    assert findings[0].name == f"watch.adr.{record.ref}"
    assert findings[0].status == CheckStatus.ERROR
    assert annotations.OVERLAY_FILENAME in findings[0].message
    assert findings[0].hint

    _ = annotations.write_overlay(record.path, {"title": "T"})
    assert annotations.annotations_checks(ctx) == []

    path.unlink()
    path.mkdir()
    findings = annotations.annotations_checks(ctx)

    assert len(findings) == 1
    assert findings[0].status == CheckStatus.ERROR
    assert annotations.OVERLAY_FILENAME in findings[0].message
    path.rmdir()


def test_classifier_check_reports_invalid_user_rules(doctor_env: SimpleNamespace) -> None:
    """The classifier check is ok normally and an error for an invalid user file."""
    state = _state()
    ctx = DoctorContext(state=state, home=doctor_env.home)

    bundled = classifier_checks(ctx)
    assert len(bundled) == 1
    assert bundled[0].name == "classify.ruleset"
    assert bundled[0].status == CheckStatus.OK
    assert "built-in rules only" in bundled[0].message

    path = doctor_env.home / ".config" / "lion" / "classifier.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    findings = classifier_checks(ctx)

    assert len(findings) == 1
    assert findings[0].status == CheckStatus.ERROR
    assert str(path) in findings[0].message
    assert findings[0].hint


def test_classifier_check_reports_the_user_file(doctor_env: SimpleNamespace) -> None:
    """A valid user file is reported with its path in the ok message."""
    state = _state()
    ctx = DoctorContext(state=state, home=doctor_env.home)
    path = doctor_env.home / ".config" / "lion" / "classifier.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "packages.custom",
                        "group": "packages",
                        "match": "^custompkg[[:space:]]+install",
                        "emit": {"manager": "custompkg", "action": "install"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    findings = classifier_checks(ctx)

    assert len(findings) == 1
    assert findings[0].status == CheckStatus.OK
    assert str(path) in findings[0].message


def _wiki_ctx(doctor_env: SimpleNamespace) -> DoctorContext:
    return DoctorContext(state=_state(), home=doctor_env.home)


def test_wiki_freshness_without_history_is_skip(doctor_env: SimpleNamespace) -> None:
    """No history is a neutral skip; the check never creates anything."""
    findings = wiki.wiki_checks(_wiki_ctx(doctor_env))

    assert len(findings) == 1
    assert findings[0].name == "wiki.freshness"
    assert findings[0].topic == "wiki"
    assert findings[0].status == CheckStatus.SKIP
    assert findings[0].message == "No history present."
    assert not get_data_dir().exists()


def test_wiki_freshness_unreadable_history_is_skip(doctor_env: SimpleNamespace) -> None:
    """A damaged history defers to the history topic instead of erroring twice."""
    history = get_history_dir()
    history.mkdir(parents=True)
    (history / "broken.toml").write_text("broken = [", encoding="utf-8")

    findings = wiki.wiki_checks(_wiki_ctx(doctor_env))

    assert findings[0].status == CheckStatus.SKIP
    assert "see the history topic" in findings[0].message


def test_wiki_freshness_missing_index_is_warn(doctor_env: SimpleNamespace) -> None:
    """A stored state without a wiki is a warn naming the reference."""
    storage.save_state(_state())
    ref = storage.list_entries()[-1].ref

    findings = wiki.wiki_checks(_wiki_ctx(doctor_env))

    assert findings[0].status == CheckStatus.WARN
    assert findings[0].message == f"Wiki is missing for the latest state ({ref})."
    assert findings[0].hint == "'lion scan' regenerates it."
    assert not wiki.get_wiki_dir().exists()


def test_wiki_freshness_unreadable_index_is_warn(doctor_env: SimpleNamespace) -> None:
    """An index without a readable frontmatter reference is a warn."""
    storage.save_state(_state())
    index = wiki.get_wiki_dir() / "index.md"
    index.parent.mkdir(parents=True)
    index.write_text("no frontmatter\n", encoding="utf-8")

    findings = wiki.wiki_checks(_wiki_ctx(doctor_env))

    assert findings[0].status == CheckStatus.WARN
    assert f"Wiki index is unreadable: {index}" in findings[0].message
    assert findings[0].hint == "'lion scan' regenerates it."


def test_wiki_freshness_stale_and_matching(doctor_env: SimpleNamespace) -> None:
    """A foreign reference is stale; the rendered wiki matches again."""
    storage.save_state(_state())
    entry = storage.list_entries()[-1]
    index = wiki.get_wiki_dir() / "index.md"
    index.parent.mkdir(parents=True)
    index.write_text("---\nsnapshot_ref: old-ref\n---\n", encoding="utf-8")

    stale = wiki.wiki_checks(_wiki_ctx(doctor_env))

    assert stale[0].status == CheckStatus.WARN
    assert stale[0].message == f"Wiki is stale (ref old-ref, latest {entry.ref})."

    _ = wiki.write_pages(wiki.render_pages(entry.snapshot, entry.ref))
    fresh = wiki.wiki_checks(_wiki_ctx(doctor_env))

    assert fresh[0].status == CheckStatus.OK
    assert fresh[0].message == f"Wiki matches the latest state ({entry.ref})."
    assert fresh[0].hint == ""


def test_wiki_freshness_stale_after_out_of_band_state(
    monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace
) -> None:
    """A state appended outside 'scan' leaves the wiki stale until the next scan."""
    _use_state(monkeypatch, _state())
    assert runner.invoke(app, ["scan"]).exit_code == 0
    changed = _state()
    changed["host"]["hostname"] = "other"
    storage.save_state(changed)

    findings = wiki.wiki_checks(_wiki_ctx(doctor_env))

    assert findings[0].status == CheckStatus.WARN
    assert "stale" in findings[0].message


def test_doctor_json_reports_fresh_wiki(monkeypatch: pytest.MonkeyPatch, doctor_env: SimpleNamespace) -> None:
    """``lion doctor --json`` carries the fresh wiki finding."""
    _use_state(monkeypatch, _state())
    storage.save_state(_state())
    entry = storage.list_entries()[-1]
    _ = wiki.write_pages(wiki.render_pages(entry.snapshot, entry.ref))

    result = _invoke("--json")

    assert result.exit_code == 0
    findings = {item["name"]: item for item in json.loads(result.stdout)["findings"]}
    assert findings["wiki.freshness"]["status"] == "ok"
    assert findings["wiki.freshness"]["message"] == f"Wiki matches the latest state ({entry.ref})."
