"""Tests for the collector runner and its failure isolation."""

from lion.state.collector import Collector, CollectorResult, CollectorStatus, collect_state


def _ok(name: str) -> Collector:
    return Collector(name=name, collect=lambda: CollectorResult(status=CollectorStatus.OK, data={"value": name}))


def test_collect_state_preserves_order_and_status() -> None:
    """Every collector contributes its data under its own name."""
    state = collect_state((_ok("first"), _ok("second")))
    assert list(state) == ["first", "second"]
    assert state["first"] == {"status": "ok", "error": "", "value": "first"}


def test_collect_state_keeps_unavailable_and_error_message() -> None:
    """A collector can report unavailability with an explanation."""

    def unavailable() -> CollectorResult:
        return CollectorResult(status=CollectorStatus.UNAVAILABLE, data={}, error="no dpkg")

    state = collect_state((Collector(name="pkg", collect=unavailable),))
    assert state["pkg"] == {"status": "unavailable", "error": "no dpkg"}


def test_collect_state_isolates_exceptions() -> None:
    """An exception in one collector becomes an error section, not an abort."""

    def boom() -> CollectorResult:
        raise RuntimeError("kaputt")

    state = collect_state((Collector(name="bad", collect=boom), _ok("good")))
    assert state["bad"]["status"] == "error"
    assert state["bad"]["error"] == "kaputt"
    assert state["good"]["status"] == "ok"
