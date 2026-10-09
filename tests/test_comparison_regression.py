"""Keep history decisions and displayed differences consistent for lists."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lion.cli import app
from lion.command import status as status_command
from lion.program.diff import diff_collectors
from lion.program.storage import get_history_dir, save_state
from lion.state.model import collectors_equal


@pytest.mark.parametrize(
    ("collector", "field", "old", "new", "equal"),
    [
        ("packages", "manual", ["bash", "zsh"], ["zsh", "bash"], True),
        ("packages", "auto", ["a", "b"], ["b", "a"], True),
        ("packages", "held", ["a", "b"], ["b", "a"], True),
        ("packages", "manual", ["bash"], ["bash", "bash"], False),
        ("packages", "manual", [1], [True], False),
        ("packages", "manual", [], [1, "1"], False),
        ("custom", "items", ["a", "b"], ["b", "a"], False),
        ("custom", "items", ["a"], ["a", "a"], False),
        ("custom", "items", [], [1, "1"], False),
        ("hardware", "gpu", [{"pci_id": "A"}, {"pci_id": "B"}], [{"pci_id": "B"}, {"pci_id": "A"}], True),
        ("hardware", "gpu", [{"pci_id": "A"}], [{"pci_id": "A"}, {"pci_id": "A"}], False),
        ("hardware", "gpu", [{"pci_id": ""}], [{"pci_id": "", "name": "new"}], False),
        ("hardware", "gpu", [{"name": "old"}], [{"pci_id": "A", "name": "old"}], False),
        ("hardware", "gpu", [], [{"pci_id": "A"}], False),
        ("hardware", "gpu", [{"pci_id": "A", "name": "old"}], [{"pci_id": "A", "name": "new"}], False),
    ],
)
def test_status_and_scan_share_list_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    collector: str,
    field: str,
    old: list[object],
    new: list[object],
    equal: bool,
) -> None:
    """Reordering, invalid identities and duplicate values cannot hide a state."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    old_state: dict[str, dict[str, object]] = {collector: {"status": "ok", "error": "", field: old}}
    new_state: dict[str, dict[str, object]] = {collector: {"status": "ok", "error": "", field: new}}
    save_state(old_state)
    before = {path.name: path.read_bytes() for path in get_history_dir().glob("*.toml")}

    def collect(collectors: object) -> dict[str, dict[str, object]]:
        return new_state

    monkeypatch.setattr(status_command, "collect_state", collect)

    result = CliRunner().invoke(app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["geaendert"] is not equal
    assert {path.name: path.read_bytes() for path in get_history_dir().glob("*.toml")} == before
    assert collectors_equal(old_state, new_state) is equal
    assert bool(diff_collectors(old_state, new_state)) is not equal
    outcome = save_state(new_state)
    assert outcome.event == ("confirmed" if equal else "appended")
    assert len(list(get_history_dir().glob("*.toml"))) == (1 if equal else 2)


def test_duplicate_gpu_ids_do_not_overwrite_entries() -> None:
    """An ambiguous PCI list falls back to the full value instead of dropping data."""
    old = [{"pci_id": "A", "name": "one"}, {"pci_id": "A", "name": "two"}]
    new = [{"pci_id": "A", "name": "two"}]
    assert diff_collectors({"hardware": {"gpu": old}}, {"hardware": {"gpu": new}}) == {
        "hardware": {"changed": {"gpu": {"old": old, "new": new}}}
    }


def test_scalar_path_collisions_keep_all_values() -> None:
    """Unrecognized lists preserve both integer and string values in JSON."""
    assert diff_collectors({"custom": {"items": []}}, {"custom": {"items": [1, "1"]}}) == {
        "custom": {"changed": {"items": {"old": [], "new": [1, "1"]}}}
    }
