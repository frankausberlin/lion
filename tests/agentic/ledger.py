"""Deterministic ledger fixtures for the agentic scenarios.

The builders run through the real program layer under the isolated
``HOME``/``XDG_DATA_HOME`` that the conftest fixtures install, mirroring
``tests/test_serve.py``. References and times are fixed so every scenario sees
the same ledger; the recording carries a synthetic secret (never a real one).
"""

import os
from collections.abc import Mapping
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
SEARCH_TOKEN = "NEEDLE-4711"
SECRET = "SYNTHETIC-MELLUM-SECRET-4711"
ADR_FIELDS: dict[str, str] = {
    "title": f"Search anchor {SEARCH_TOKEN}",
    "decision": "The search scenario finds this standalone decision.",
}
RECORDING_EVENTS: tuple[tuple[str, str], ...] = (
    ("cmd", "apt-get install -y ripgrep"),
    ("result", "0\t1.5\t2.25"),
    ("cmd", f"export API_KEY={SECRET}"),
    ("result", "0\t0.1\t0.05"),
    ("comment", "# the token stays masked"),
)


def write_state(ref: str, iso: str, collectors: Mapping[str, Mapping[str, object]]) -> None:
    """Write one stored state exactly like a scan would."""
    get_history_dir().mkdir(parents=True, exist_ok=True)
    snapshot = Snapshot(created_at=iso, confirmed_at=iso, collectors={k: dict(v) for k, v in collectors.items()})
    (get_history_dir() / f"{ref}.toml").write_text(tomli_w.dumps(snapshot.to_toml_dict()))


def two_states() -> None:
    """Seed the fixed A/B states (A older, B latest)."""
    write_state(A_REF, A_ISO, HOST_STATE)
    write_state(B_REF, B_ISO, OTHER_STATE)


def search_adr() -> str:
    """Seed one standalone ADR carrying the unique search token."""
    return adrs.create_adr(dict(ADR_FIELDS)).ref


def secret_recording() -> str:
    """Seed one stopped recording with classified commands and a synthetic secret."""
    record = recording.start_recording(dict(HOST_STATE), "agentic fixture")
    base = datetime.now(UTC).timestamp() - 60
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
    ticks = int(stat.rsplit(")", 1)[-1].split()[19])
    lines = [f"segment\t{boot}\t{os.getpid()}\t{ticks}\t{base}"]
    for index, (kind, text) in enumerate(RECORDING_EVENTS):
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
