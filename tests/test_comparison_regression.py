"""Keep history decisions and displayed differences consistent for lists."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lion.cli import app
from lion.command import status as status_command
from lion.program.diff import diff_collectors
from lion.program.storage import get_history_dir, save_state
from lion.state.comparison import list_items
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
        ("network", "interfaces", [{"name": "eth0"}, {"name": "eth1"}], [{"name": "eth1"}, {"name": "eth0"}], True),
        ("network", "interfaces", [{"name": "eth0"}], [{"name": "eth0"}, {"name": "eth0"}], False),
        ("network", "interfaces", [{"mac": "x"}], [{"name": "eth0", "mac": "x"}], False),
        (
            "services",
            "units",
            [{"name": "a.service"}, {"name": "b.service"}],
            [{"name": "b.service"}, {"name": "a.service"}],
            True,
        ),
        ("services", "units", [{"name": ""}], [{"name": "", "state": "enabled"}], False),
        ("containers", "containers", [{"name": "one"}, {"name": "two"}], [{"name": "two"}, {"name": "one"}], True),
        ("containers", "volumes", [{"name": "v1"}, {"name": "v2"}], [{"name": "v2"}, {"name": "v1"}], True),
        ("containers", "networks", [{"name": "n1"}, {"name": "n2"}], [{"name": "n2"}, {"name": "n1"}], True),
        (
            "containers",
            "images",
            [{"repository": "a", "tag": "1"}, {"repository": "b", "tag": "2"}],
            [{"repository": "b", "tag": "2"}, {"repository": "a", "tag": "1"}],
            True,
        ),
        (
            "containers",
            "images",
            [{"repository": "a", "tag": "", "digest": "sha256:x"}],
            [{"repository": "a", "tag": "", "digest": "sha256:x"}],
            True,
        ),
        (
            "containers",
            "images",
            [
                {"repository": "a", "tag": "<none>", "digest": "sha256:x"},
                {"repository": "b", "tag": "<none>", "digest": "sha256:y"},
            ],
            [
                {"repository": "b", "tag": "<none>", "digest": "sha256:y"},
                {"repository": "a", "tag": "<none>", "digest": "sha256:x"},
            ],
            True,
        ),
        (
            "containers",
            "images",
            [{"repository": "a", "tag": "1"}],
            [{"repository": "a", "tag": "2"}],
            False,
        ),
        (
            "containers",
            "images",
            [{"repository": "a", "tag": "1"}],
            [{"repository": "a", "tag": "1"}, {"repository": "a", "tag": "1"}],
            False,
        ),
        ("containers", "images", [{"tag": "1"}], [{"repository": "a", "tag": "1"}], False),
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
    assert json.loads(result.stdout)["changed"] is not equal
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


def test_image_identity_prefers_tag_then_digest() -> None:
    """A container image is identified by its tag, or by its digest when untagged."""
    tagged = {"repository": "nginx", "tag": "latest"}
    assert list_items("containers.images", [tagged]) == {"nginx:latest": tagged}
    untagged = {"repository": "nginx", "tag": "<none>", "digest": "sha256:abc"}
    assert list_items("containers.images", [untagged]) == {"nginx@sha256:abc": untagged}


def test_unknown_or_conflicting_identities_stay_atomic() -> None:
    """Duplicate, missing or non-mapping identities fall back to ordered comparison."""
    assert list_items("network.interfaces", [{"name": "eth0"}, {"name": "eth0"}]) is None
    assert list_items("services.units", [{"name": ""}]) is None
    assert list_items("containers.volumes", ["not-a-mapping"]) is None
    assert list_items("custom.items", ["a", "b"]) is None


def test_interface_change_uses_identity_keys() -> None:
    """A removed network interface appears under its bracket identity key."""
    old = {"network": {"interfaces": [{"name": "eth0", "mac": "aa"}, {"name": "eth1", "mac": "bb"}]}}
    new = {"network": {"interfaces": [{"name": "eth1", "mac": "bb"}]}}
    assert diff_collectors(old, new) == {"network": {"removed": {"interfaces[eth0]": {"name": "eth0", "mac": "aa"}}}}
