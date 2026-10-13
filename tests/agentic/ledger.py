"""Deterministic ledger fixtures for the agentic scenarios.

The state and recording builders live in :mod:`tests.ledger_fixtures` (shared
with ``tests/test_serve.py``), so the persisted format can never drift between
the two suites. Only the agentic-specific seed values live here; the recording
carries a synthetic secret (never a real one).
"""

from tests.ledger_fixtures import add_adr, stopped_recording

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


def search_adr() -> str:
    """Seed one standalone ADR carrying the unique search token."""
    return add_adr(ADR_FIELDS)


def secret_recording() -> str:
    """Seed one stopped recording with classified commands and a synthetic secret."""
    return stopped_recording(RECORDING_EVENTS, note="agentic fixture")
