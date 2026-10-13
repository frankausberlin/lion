"""Tests for the derived OKF v0.2 wiki: rendering, writing and healing.

Rendering is a pure function of the validated snapshot, so these tests pin the
exact page text, the frontmatter key order, the palette and the adversarial
string rules. The writer and the ``scan`` integration run against temporary
data directories.
"""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lion.cli import app
from lion.command import scan as scan_command
from lion.program import wiki
from lion.program.storage import list_entries, load_latest
from lion.state.model import Snapshot
from lion.state.registry import COLLECTORS

runner = CliRunner()

REF = "2026-10-13T12-00-00.000000Z"
CREATED = "2026-10-13T12:00:00+00:00"

_FRONT = 'okf_version: "0.2"\n'


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the wiki and history away from the real data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def _state() -> dict[str, dict[str, object]]:
    return {
        "host": {"status": "ok", "error": "", "hostname": "lion", "kernel": "6.8.0"},
        "hardware": {
            "status": "ok",
            "error": "",
            "cpu_model": "Test CPU",
            "cuda_version": "",
            "gpu": [{"pci_id": "0000:01:00.0", "vendor": "nvidia", "driver_version": ""}],
        },
        "network": {"status": "ok", "error": "", "interfaces": []},
        "packages": {
            "status": "unavailable",
            "error": "Dpkg: unavailable",
            "manual": ["vim", "zsh"],
            "auto": [],
            "held": [],
        },
        "services": {"status": "ok", "error": "", "units": [{"name": "sshd.service", "state": "enabled"}]},
        "containers": {
            "status": "ok",
            "error": "",
            "runtime": "docker",
            "containers": [
                {
                    "name": "web",
                    "ports": [{"container_port": 80, "protocol": "tcp", "host_port": ""}],
                }
            ],
        },
        "tools": {"status": "ok", "error": "", "available": {"lspci": True, "zsh": False}},
        "dash": {"status": "ok", "error": "", "files": [{"name": "LIONSTATE_x", "hash": "ab"}]},
    }


def _snapshot(state: dict[str, dict[str, object]] | None = None) -> Snapshot:
    return Snapshot(created_at=CREATED, confirmed_at=CREATED, collectors=state if state is not None else _state())


def _use_state(monkeypatch: pytest.MonkeyPatch, state: dict[str, dict[str, object]]) -> None:
    def fake_collect(_collectors: object) -> dict[str, dict[str, object]]:
        return state

    monkeypatch.setattr(scan_command, "collect_state", fake_collect)


def test_render_pages_is_deterministic() -> None:
    """The same snapshot always renders byte-identical pages."""
    first = wiki.render_pages(_snapshot(), REF)
    second = wiki.render_pages(_snapshot(), REF)

    assert first == second
    assert all(text.endswith("\n") for text in first.values())
    assert all("\r" not in text for text in first.values())


def test_page_set_and_publication_order() -> None:
    """Every registered collector gets a page, the index comes last."""
    pages = wiki.render_pages(_snapshot(), REF)

    assert list(pages) == [f"{collector.name}.md" for collector in COLLECTORS] + ["index.md"]


def test_collector_page_exact_text() -> None:
    """A healthy collector page carries the fixed frontmatter and facts."""
    pages = wiki.render_pages(_snapshot(), REF)

    assert pages["host.md"] == (
        "---\n"
        f"{_FRONT}"
        f"snapshot_ref: {REF}\n"
        "collector: host\n"
        "status: ok\n"
        "color: blue\n"
        "title: Host\n"
        "---\n"
        "\n"
        "# Host\n"
        "\n"
        "## Facts\n"
        "- **hostname**: lion\n"
        "- **kernel**: 6.8.0\n"
    )


def test_unavailable_section_keeps_error_and_lists() -> None:
    """A non-empty error is a fact; empty lists render as ``(none)``."""
    pages = wiki.render_pages(_snapshot(), REF)

    assert pages["packages.md"] == (
        "---\n"
        f"{_FRONT}"
        f"snapshot_ref: {REF}\n"
        "collector: packages\n"
        "status: unavailable\n"
        "color: amber\n"
        "title: Packages\n"
        "---\n"
        "\n"
        "# Packages\n"
        "\n"
        "## Facts\n"
        "- **error**: Dpkg: unavailable\n"
        "- **manual**:\n"
        "  - vim\n"
        "  - zsh\n"
        "- **auto**: (none)\n"
        "- **held**: (none)\n"
    )


def test_table_lists_and_nested_cells() -> None:
    """Table entries render as ``key=value``; nested values become compact JSON."""
    pages = wiki.render_pages(_snapshot(), REF)

    assert '  - pci_id=0000:01:00.0, vendor=nvidia, driver_version=""\n' in pages["hardware.md"]
    assert "  - name=sshd.service, state=enabled\n" in pages["services.md"]
    assert "  - name=LIONSTATE_x, hash=ab\n" in pages["dash.md"]
    assert '  - name=web, ports=[{"container_port":80,"protocol":"tcp","host_port":""}]\n' in pages["containers.md"]
    assert '- **available**: {"lspci":true,"zsh":false}\n' in pages["tools.md"]


def test_index_exact_text() -> None:
    """The index names the snapshot facts and links every collector page."""
    pages = wiki.render_pages(_snapshot(), REF)

    assert pages["index.md"] == (
        "---\n"
        f"{_FRONT}"
        f"snapshot_ref: {REF}\n"
        "color: slate\n"
        "title: LION system wiki\n"
        "---\n"
        "\n"
        "# LION system wiki\n"
        "\n"
        f"- **created_at**: {CREATED}\n"
        f"- **confirmed_at**: {CREATED}\n"
        "\n"
        "## Collectors\n"
        "\n"
        "- [host](host.md)\n"
        "- [hardware](hardware.md)\n"
        "- [network](network.md)\n"
        "- [packages](packages.md)\n"
        "- [services](services.md)\n"
        "- [containers](containers.md)\n"
        "- [tools](tools.md)\n"
        "- [dash](dash.md)\n"
    )


def test_frontmatter_key_order() -> None:
    """Frontmatter keys follow the documented order on every page."""
    pages = wiki.render_pages(_snapshot(), REF)

    for name, text in pages.items():
        lines = text.splitlines()
        assert lines[0] == "---"
        end = lines.index("---", 1)
        keys = [line.partition(":")[0] for line in lines[1:end]]
        expected = ["okf_version", "snapshot_ref"]
        if name != "index.md":
            expected += ["collector", "status"]
        expected += ["color", "title"]
        assert keys == expected, name
        assert lines[end + 1] == ""


def test_palette_is_frozen() -> None:
    """The 9-token identity palette and its collector mapping are public API."""
    assert wiki.PALETTE == {
        "slate": "#64748B",
        "blue": "#3B82F6",
        "violet": "#8B5CF6",
        "teal": "#14B8A6",
        "amber": "#F59E0B",
        "green": "#22C55E",
        "orange": "#F97316",
        "cyan": "#06B6D4",
        "rose": "#F43F5E",
    }
    assert wiki.IDENTITY_COLORS == {
        "index": "slate",
        "host": "blue",
        "hardware": "violet",
        "network": "teal",
        "packages": "amber",
        "services": "green",
        "containers": "orange",
        "tools": "cyan",
        "dash": "rose",
    }
    assert set(wiki.IDENTITY_COLORS.values()) <= set(wiki.PALETTE)
    assert {collector.name for collector in COLLECTORS} | {"index"} == set(wiki.IDENTITY_COLORS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  a\t\tb  ", "a b"),
        ("", '""'),
        ("---", "---"),
        ("x\ny", "x y"),
        ("line1\r\nline2", "line1 line2"),
    ],
)
def test_string_values_are_one_line(value: str, expected: str) -> None:
    """Hostile strings cannot break the frontmatter or the fact format."""
    state = _state()
    state["host"]["hostname"] = value

    host = wiki.render_pages(_snapshot(state), REF)["host.md"]

    assert f"- **hostname**: {expected}\n" in host
    assert [line for line in host.splitlines() if line == "---"] == ["---", "---"]


def test_read_snapshot_ref_roundtrip() -> None:
    """The doctor-side frontmatter reader understands the rendered grammar."""
    read_ref = wiki._read_snapshot_ref  # pyright: ignore[reportPrivateUsage]
    wiki_dir = wiki.get_wiki_dir()
    _ = wiki.write_pages(wiki.render_pages(_snapshot(), REF))
    assert read_ref(wiki_dir / "index.md") == REF
    assert read_ref(wiki_dir / "host.md") == REF

    (wiki_dir / "index.md").write_text("no frontmatter\n", encoding="utf-8")
    assert read_ref(wiki_dir / "index.md") is None
    (wiki_dir / "index.md").write_text("---\ncolor: slate\n---\n", encoding="utf-8")
    assert read_ref(wiki_dir / "index.md") is None
    (wiki_dir / "index.md").unlink()
    (wiki_dir / "index.md").mkdir()
    assert read_ref(wiki_dir / "index.md") is None


def test_non_string_scalars_render_plainly() -> None:
    """``None``, booleans and numbers have stable one-line renderings."""
    state = _state()
    state["host"]["hostname"] = None
    state["host"]["kernel"] = 3
    state["host"]["distribution"] = 1.5
    state["host"]["architecture"] = True

    host = wiki.render_pages(_snapshot(state), REF)["host.md"]

    assert "- **hostname**: null\n" in host
    assert "- **kernel**: 3\n" in host
    assert "- **distribution**: 1.5\n" in host
    assert "- **architecture**: true\n" in host


def test_write_pages_replaces_each_file_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every page is published via ``os.replace`` and no staging file survives."""
    pages = wiki.render_pages(_snapshot(), REF)
    replaced: list[str] = []
    original = os.replace

    def spy(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        replaced.append(Path(target).name)
        original(source, target)

    monkeypatch.setattr(wiki.os, "replace", spy)

    written = wiki.write_pages(pages)

    assert replaced == list(pages)
    assert [path.name for path in written] == list(pages)
    assert not list(wiki.get_wiki_dir().glob("*.tmp"))
    assert (wiki.get_wiki_dir() / "host.md").read_text(encoding="utf-8") == pages["host.md"]


def test_write_pages_overwrites_existing_content() -> None:
    """A rerun replaces the previous page content."""
    wiki_dir = wiki.get_wiki_dir()
    _ = wiki.write_pages(wiki.render_pages(_snapshot(), REF))
    (wiki_dir / "host.md").write_text("stale", encoding="utf-8")

    _ = wiki.write_pages(wiki.render_pages(_snapshot(), REF))

    assert (wiki_dir / "host.md").read_text(encoding="utf-8") == wiki.render_pages(_snapshot(), REF)["host.md"]


def test_write_error_names_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing page write reports the affected page path."""

    def fail(*args: object, **kwargs: object) -> object:
        raise OSError("denied")

    monkeypatch.setattr(wiki.tempfile, "mkstemp", fail)

    with pytest.raises(OSError) as info:
        _ = wiki.write_pages({"host.md": "x"})

    assert "host.md" in str(info.value)
    assert "denied" in str(info.value)


def test_write_error_names_the_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unusable wiki directory fails loudly with its path."""
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    monkeypatch.setattr(wiki, "get_wiki_dir", lambda: blocker / "wiki")

    with pytest.raises(OSError) as info:
        _ = wiki.write_pages({"host.md": "x"})

    assert str(blocker / "wiki") in str(info.value)


def test_scan_writes_wiki_for_created_confirmed_and_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every scan regenerates the wiki for the snapshot reference it stored."""
    state = _state()
    _use_state(monkeypatch, state)

    first = runner.invoke(app, ["scan"])
    assert first.exit_code == 0
    assert "State created" in first.stdout
    entries = list_entries()
    assert len(entries) == 1
    ref = entries[-1].ref
    for collector in COLLECTORS:
        assert (wiki.get_wiki_dir() / f"{collector.name}.md").is_file()
    assert f"snapshot_ref: {ref}\n" in (wiki.get_wiki_dir() / "index.md").read_text(encoding="utf-8")
    assert f"snapshot_ref: {ref}\n" in (wiki.get_wiki_dir() / "host.md").read_text(encoding="utf-8")

    second = runner.invoke(app, ["scan"])
    assert second.exit_code == 0
    assert "Timestamp refreshed" in second.stdout
    assert list_entries()[-1].ref == ref
    assert f"snapshot_ref: {ref}\n" in (wiki.get_wiki_dir() / "index.md").read_text(encoding="utf-8")

    state["host"]["hostname"] = "server"
    third = runner.invoke(app, ["scan"])
    assert third.exit_code == 0
    assert "New state saved" in third.stdout
    new_ref = list_entries()[-1].ref
    assert new_ref != ref
    assert f"snapshot_ref: {new_ref}\n" in (wiki.get_wiki_dir() / "index.md").read_text(encoding="utf-8")


def test_scan_render_error_exits_loudly_and_keeps_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """A render failure aborts after the save: loud error, valid history, no wiki."""
    _use_state(monkeypatch, _state())

    def boom(_snapshot: Snapshot, _ref: str) -> dict[str, str]:
        raise ValueError("render exploded")

    monkeypatch.setattr(scan_command, "render_pages", boom)

    result = runner.invoke(app, ["scan"])

    assert result.exit_code == 1
    assert "render exploded" in result.stderr
    assert "State created" not in result.stdout
    latest = load_latest()
    assert latest is not None
    assert latest.collectors["host"]["hostname"] == "lion"
    assert not (wiki.get_wiki_dir() / "index.md").exists()


def test_scan_write_error_after_save_heals_on_the_next_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wiki write failure keeps the stored state; the next scan heals the wiki."""
    _use_state(monkeypatch, _state())

    def boom(_pages: object) -> object:
        raise OSError("disk full")

    with monkeypatch.context() as scoped:
        scoped.setattr(scan_command, "write_pages", boom)
        failed = runner.invoke(app, ["scan"])
        assert failed.exit_code == 1
        assert "disk full" in failed.stderr
        assert load_latest() is not None
        assert not (wiki.get_wiki_dir() / "index.md").exists()

    healed = runner.invoke(app, ["scan"])
    assert healed.exit_code == 0
    ref = list_entries()[-1].ref
    assert f"snapshot_ref: {ref}\n" in (wiki.get_wiki_dir() / "index.md").read_text(encoding="utf-8")


def test_scan_regenerates_a_damaged_wiki(monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged index is replaced by the next scan."""
    _use_state(monkeypatch, _state())
    wiki_dir = wiki.get_wiki_dir()
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "index.md").write_text("garbage", encoding="utf-8")

    result = runner.invoke(app, ["scan"])

    assert result.exit_code == 0
    ref = list_entries()[-1].ref
    assert f"snapshot_ref: {ref}\n" in (wiki_dir / "index.md").read_text(encoding="utf-8")


def test_scan_json_stays_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON contract carries no index and no wiki path."""
    _use_state(monkeypatch, _state())

    result = runner.invoke(app, ["scan", "--json"])

    assert result.exit_code == 0
    assert "\u25cf" not in result.stdout
    assert "wiki" not in result.stdout
