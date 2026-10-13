"""Shared ledger builders for the serve and agentic test suites.

Both suites seed the same deterministic ledger through the real program layer
under their own isolated ``HOME``/``XDG_DATA_HOME``: the fixed references, the
state writer, the two standard states and the raw recording-segment writer live
here once, so a change to the persisted format can never update only one suite.
Suite-specific seed values (extra states, ADR fields, event scripts) stay in
the suite that owns them.
"""

import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import tomli_w

from lion.program import adrs, recording
from lion.program.storage import get_history_dir
from lion.state.model import Snapshot

A_REF = "2026-10-05T18-00-00.000000Z"
A_ISO = "2026-10-05T18:00:00+00:00"
B_REF = "2026-10-05T20-00-00.000000Z"
B_ISO = "2026-10-05T20:00:00+00:00"
HOST_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "lion"}}
OTHER_STATE: dict[str, dict[str, object]] = {"host": {"status": "ok", "error": "", "hostname": "other"}}


def write_state(ref: str, iso: str, collectors: Mapping[str, Mapping[str, object]]) -> None:
    """Write one stored state exactly like a scan would."""
    get_history_dir().mkdir(parents=True, exist_ok=True)
    snapshot = Snapshot(created_at=iso, confirmed_at=iso, collectors={k: dict(v) for k, v in collectors.items()})
    (get_history_dir() / f"{ref}.toml").write_text(tomli_w.dumps(snapshot.to_toml_dict()))


def two_states() -> None:
    """Seed the fixed A/B states (A older, B latest)."""
    write_state(A_REF, A_ISO, HOST_STATE)
    write_state(B_REF, B_ISO, OTHER_STATE)


def add_adr(fields: Mapping[str, str], now: datetime | None = None) -> str:
    """Create one standalone ADR and return its reference."""
    return adrs.create_adr(dict(fields), now=now).ref


def stopped_recording(events: Sequence[tuple[str, str]], note: str = "") -> str:
    """Create one stopped recording whose single segment carries the events."""
    record = recording.start_recording(dict(HOST_STATE), note)
    base = datetime.now(UTC).timestamp() - 60
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
    ticks = int(stat.rsplit(")", 1)[-1].split()[19])
    lines = [f"segment\t{boot}\t{os.getpid()}\t{ticks}\t{base}"]
    for index, (kind, text) in enumerate(events):
        stamp = base + index + 1
        if kind in ("cmd", "comment"):
            lines.append(f"{kind}\t{stamp}\t/tmp\t{text}")
        else:
            lines.append(f"{kind}\t{stamp}\t{text}")
    directory = recording.get_recordings_dir() / record.ref / "events"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "100-1-1.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _ = recording.stop_recording(dict(HOST_STATE))
    return record.ref
