"""End-to-end coverage for the derived wiki written by ``scan``.

A fresh environment scans with real permissions: the wiki appears with one
page per collector and an index whose ``snapshot_ref`` matches the stored
history reference. ``status`` shows the shared index and points at the wiki
without writing, ``doctor`` reports it fresh, and ``lstat`` manifests prove
that no read-only command touches the history or the wiki.
"""

import json
import re
from pathlib import Path
from typing import cast

import pytest
from e2eutil import Runner, doctor_findings, manifest

pytestmark = pytest.mark.e2e

_COLLECTORS = ("host", "hardware", "network", "packages", "services", "containers", "tools", "dash")
_LINK = re.compile(r"- \[(?P<name>[a-z]+)\]\((?P<target>[a-z]+)\.md\)")
_BULLET = "\u25cf"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return cast("dict[str, object]", value)


def _wiki(data: Path) -> Path:
    return data / "lion" / "wiki"


def test_scan_writes_the_wiki_and_read_only_commands_preserve_it(runner: Runner, env: dict[str, str]) -> None:
    """One fresh-home journey: scan writes the wiki, status and doctor only read."""
    data = Path(env["XDG_DATA_HOME"])
    home = Path(env["HOME"])
    home_before = manifest(home)

    scan = runner.invoke("lion", "scan", env=env, expected=0)

    entry = next((data / "lion" / "history").glob("*.toml"))
    wiki = _wiki(data)
    index = (wiki / "index.md").read_text(encoding="utf-8")
    assert f"snapshot_ref: {entry.stem}\n" in index
    assert "title: LION system wiki\n" in index
    assert "- **created_at**:" in index
    assert "- **confirmed_at**:" in index
    assert [match.group("name") for match in _LINK.finditer(index)] == list(_COLLECTORS)
    for name in _COLLECTORS:
        page = (wiki / f"{name}.md").read_text(encoding="utf-8")
        assert page.startswith("---\n"), name
        assert f"collector: {name}\n" in page, name
        assert f"title: {name.capitalize()}\n" in page, name
        assert f"snapshot_ref: {entry.stem}\n" in page, name

    assert "\x1b" not in scan.stdout
    scan_lines = [line for line in scan.stdout.splitlines() if line.startswith(f"  {_BULLET} ")]
    assert len(scan_lines) == len(_COLLECTORS)
    for name in _COLLECTORS:
        assert any(line.startswith(f"  {_BULLET} {name.ljust(10)}  ") for line in scan_lines), name
    assert all(line.endswith("new") for line in scan_lines)
    assert f"wiki: {wiki / 'index.md'}" in scan.stdout
    assert manifest(home) == home_before

    data_before = manifest(data)
    status = runner.invoke("lion", "status", env=env, expected=0)
    assert "\x1b" not in status.stdout
    status_lines = [line for line in status.stdout.splitlines() if line.startswith(f"  {_BULLET} ")]
    assert len(status_lines) == len(_COLLECTORS)
    assert all(line.endswith("unchanged") for line in status_lines)
    assert "Nothing has changed" in status.stdout
    assert f"wiki: {wiki / 'index.md'}" in status.stdout
    assert manifest(data) == data_before

    history_before = manifest(data / "lion" / "history")
    wiki_before = manifest(wiki)
    payload = _mapping(json.loads(runner("lion", "doctor", "--json", env=env)))
    findings = doctor_findings(payload)
    checked = payload["checked"]
    assert isinstance(checked, list)
    assert "wiki" in checked
    assert findings["wiki.freshness"]["status"] == "ok"
    assert "matches the latest state" in str(findings["wiki.freshness"]["message"])
    assert manifest(data / "lion" / "history") == history_before
    assert manifest(wiki) == wiki_before
