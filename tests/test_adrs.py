"""Tests for the standalone ADR store (:mod:`lion.program.adrs`).

The store is isolated via ``HOME``/``XDG_DATA_HOME`` and documents are created
through the real API with a fixed clock, so the tests stay deterministic and
never touch the developer's machine.
"""

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lion.program import adrs
from lion.program.checks import CheckStatus, DoctorContext

T0 = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=5)

FIELD_BODY = 'schema_version = 1\nid = "{ref}"\ncreated_at = "{created}"\n\n[fields]\ntitle = "T"\n'


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disposable home and data directories."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _ctx(tmp_path: Path) -> DoctorContext:
    return DoctorContext(state={}, home=tmp_path)


# --- create ----------------------------------------------------------------


def test_create_publishes_mode_600_and_dir_700() -> None:
    """A new ADR lands as a mode-600 file in a mode-700 store."""
    document = adrs.create_adr({"title": "Title", "decision": "Because", "extra": "kept"}, now=T0)

    assert document.ref == "adr-2026-10-13T12-00-00.000000Z"
    assert document.path.name == f"{document.ref}.toml"
    assert stat.S_IMODE(document.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(adrs.get_adrs_dir().stat().st_mode) == 0o700
    assert document.recording == ""
    assert document.edited is False
    assert document.complete is True
    assert list(document.fields) == ["title", "decision", "extra"]


def test_create_collision_adds_suffix_and_never_overwrites() -> None:
    """The same instant yields a ``~NNNN`` suffix and keeps the first document."""
    first = adrs.create_adr({"title": "First"}, now=T0)
    second = adrs.create_adr({"title": "Second"}, now=T0)

    assert first.ref == "adr-2026-10-13T12-00-00.000000Z"
    assert second.ref == "adr-2026-10-13T12-00-00.000000Z~0001"
    assert first.path.read_bytes() != second.path.read_bytes()
    assert adrs.load_adr(first.path).fields["title"] == "First"
    assert adrs.load_adr(second.path).fields["title"] == "Second"


def test_create_rejects_empty_fields() -> None:
    """An ADR without any field is a loud error."""
    with pytest.raises(adrs.AdrError, match="at least one field"):
        _ = adrs.create_adr({}, now=T0)


def test_create_stores_recording_reference() -> None:
    """An optional recording reference is stored and read back."""
    document = adrs.create_adr({"title": "T"}, recording_ref="2026-01-01T00-00-00.000000Z", now=T0)

    assert document.recording == "2026-01-01T00-00-00.000000Z"
    assert adrs.load_adr(document.path).recording == "2026-01-01T00-00-00.000000Z"


def test_create_rejects_invalid_recording_before_publishing() -> None:
    """A whitespace-only recording fails loudly before any document is published."""
    with pytest.raises(adrs.AdrError, match="recording must be a non-empty string"):
        _ = adrs.create_adr({"title": "T"}, recording_ref="   ", now=T0)

    store = adrs.get_adrs_dir()
    assert not store.exists() or list(store.iterdir()) == []


# --- load and validation ---------------------------------------------------


def test_load_rejects_every_invalid_document(tmp_path: Path) -> None:
    """Every structural violation fails loudly naming the path."""
    directory = adrs.get_adrs_dir()
    directory.mkdir(parents=True)
    path = directory / "adr-x.toml"
    cases = [
        "broken = [",
        'schema_version = 2\nid = "adr-x"\ncreated_at = "2026-01-01T00:00:00+00:00"\n[fields]\ntitle = "T"\n',
        'schema_version = 1\ncreated_at = "2026-01-01T00:00:00+00:00"\n[fields]\ntitle = "T"\n',
        'schema_version = 1\nid = "adr-x"\n[fields]\ntitle = "T"\n',
        'schema_version = 1\nid = "adr-x"\ncreated_at = "not-a-time"\n[fields]\ntitle = "T"\n',
        'schema_version = 1\nid = "adr-x"\ncreated_at = "2026-01-01T00:00:00"\n[fields]\ntitle = "T"\n',
        'schema_version = 1\nid = "adr-x"\ncreated_at = "2026-01-01T00:00:00+00:00"\n[fields]\ntitle = ""\n',
        'schema_version = 1\nid = "adr-x"\ncreated_at = "2026-01-01T00:00:00+00:00"\n',
        'schema_version = 1\nid = "adr-x"\ncreated_at = "2026-01-01T00:00:00+00:00"\n'
        'recording = ""\n[fields]\ntitle = "T"\n',
    ]
    for content in cases:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(adrs.AdrError) as excinfo:
            _ = adrs.load_adr(path)
        assert str(path) in str(excinfo.value), content


def test_load_rejects_id_mismatch(tmp_path: Path) -> None:
    """An id that does not match the file name is a defect, never skipped."""
    directory = adrs.get_adrs_dir()
    directory.mkdir(parents=True)
    path = directory / "adr-wrong.toml"
    path.write_text(FIELD_BODY.format(ref="adr-other", created=T0.isoformat()), encoding="utf-8")

    with pytest.raises(adrs.AdrError, match="does not match the file name"):
        _ = adrs.load_adr(path)


def test_load_rejects_non_regular_file(tmp_path: Path) -> None:
    """A directory or symlink is not a document."""
    directory = adrs.get_adrs_dir()
    directory.mkdir(parents=True)
    path = directory / "adr-dir.toml"
    path.mkdir()
    with pytest.raises(adrs.AdrError, match="not a regular file"):
        _ = adrs.load_adr(path)
    path.rmdir()

    target = directory / "adr-real.toml"
    target.write_text(FIELD_BODY.format(ref="adr-real", created=T0.isoformat()), encoding="utf-8")
    link = directory / "adr-link.toml"
    link.symlink_to(target)
    with pytest.raises(adrs.AdrError, match="not a regular file"):
        _ = adrs.load_adr(link)


def test_list_rejects_unexpected_entries() -> None:
    """Any unexpected entry in the store fails loudly instead of being skipped."""
    directory = adrs.get_adrs_dir()
    directory.mkdir(parents=True)
    (directory / "notes.txt").write_text("x", encoding="utf-8")

    with pytest.raises(adrs.AdrError, match="Unexpected entry"):
        _ = adrs.list_adrs()


def test_list_rejects_non_directory_store() -> None:
    """A store path that is not a directory is a loud error."""
    adrs.get_adrs_dir().parent.mkdir(parents=True)
    adrs.get_adrs_dir().write_text("x", encoding="utf-8")

    with pytest.raises(adrs.AdrError, match="not a directory"):
        _ = adrs.list_adrs()


def test_list_missing_store_is_empty() -> None:
    """A missing store is an empty list, not an error."""
    assert adrs.list_adrs() == []


def test_list_orders_by_creation() -> None:
    """Listings are oldest first by creation time."""
    second = adrs.create_adr({"title": "Second"}, now=T1)
    first = adrs.create_adr({"title": "First"}, now=T0)

    assert [document.ref for document in adrs.list_adrs()] == [first.ref, second.ref]


def test_unknown_status_is_an_issue_not_an_error() -> None:
    """An unknown status stays verbatim and becomes a visible issue."""
    document = adrs.create_adr({"title": "T", "status": "bogus"}, now=T0)

    assert document.status == "bogus"
    assert [issue.reason for issue in document.issues] == ["unknown status value"]
    assert document.issues[0].segment == document.path.name


# --- update and write ------------------------------------------------------


def test_update_merges_fields_and_stamps_edited_at() -> None:
    """Update overwrites and adds fields, sets edited_at and keeps created_at."""
    document = adrs.create_adr({"title": "T", "decision": "D"}, now=T0)

    updated = adrs.update_adr(document, {"decision": "D2", "context": "C"}, now=T1)

    assert updated.ref == document.ref
    assert updated.created_at == document.created_at
    assert updated.fields == {"title": "T", "decision": "D2", "context": "C"}
    assert updated.edited_at == T1.isoformat()
    assert updated.edited is True
    assert stat.S_IMODE(updated.path.stat().st_mode) == 0o600
    assert not [entry for entry in updated.path.parent.iterdir() if entry.name.startswith(".lion.")]


def test_update_can_set_recording_and_rejects_empty_fields() -> None:
    """Update updates the recording reference and rejects an empty field set."""
    document = adrs.create_adr({"title": "T"}, now=T0)

    updated = adrs.update_adr(document, {"extra": "x"}, recording_ref="rec-ref", now=T1)
    assert updated.recording == "rec-ref"

    with pytest.raises(adrs.AdrError, match="at least one field"):
        _ = adrs.update_adr(document, {}, now=T1)


def test_update_rejects_invalid_values_and_keeps_the_document_byte_identical() -> None:
    """An invalid recording or field value never replaces the stored document."""
    document = adrs.create_adr({"title": "T"}, now=T0)
    before = document.path.read_bytes()

    with pytest.raises(adrs.AdrError, match="recording must be a non-empty string"):
        _ = adrs.update_adr(document, {"extra": "x"}, recording_ref="   ", now=T1)
    assert document.path.read_bytes() == before

    with pytest.raises(adrs.AdrError, match="field 'title' must be a non-empty string"):
        _ = adrs.update_adr(document, {"title": "   "}, now=T1)
    assert document.path.read_bytes() == before

    assert adrs.load_adr(document.path).fields == {"title": "T"}


def test_write_rejects_empty_fields() -> None:
    """The low-level writer rejects an empty document."""
    document = adrs.create_adr({"title": "T"}, now=T0)

    with pytest.raises(adrs.AdrError, match="at least one field"):
        _ = adrs.write_adr(document, fields={}, recording_ref="", edited_at=T1.isoformat())


# --- resolution ------------------------------------------------------------


def test_resolve_exact_prefix_and_latest() -> None:
    """Exact ids, unique prefixes and ``latest`` all resolve."""
    first = adrs.create_adr({"title": "First"}, now=T0)
    second = adrs.create_adr({"title": "Second"}, now=T1)

    assert adrs.resolve_adr(first.ref) == first
    assert adrs.resolve_adr(first.ref[:28]) == first
    assert adrs.resolve_adr("latest") == second
    assert adrs.resolve_adr("head") == second


def test_resolve_ambiguous_unknown_and_empty() -> None:
    """Ambiguous, unknown and empty references fail loudly."""
    _ = adrs.create_adr({"title": "First"}, now=T0)
    _ = adrs.create_adr({"title": "Second"}, now=T1)

    with pytest.raises(adrs.AdrError, match="ambiguous"):
        _ = adrs.resolve_adr("adr-2026-10-13T12")
    with pytest.raises(adrs.AdrError, match="Unknown reference"):
        _ = adrs.resolve_adr("does-not-exist")
    with pytest.raises(adrs.AdrError, match="Empty reference"):
        _ = adrs.resolve_adr("   ")


def test_resolve_without_store() -> None:
    """Resolving against an empty store is a loud error."""
    with pytest.raises(adrs.AdrError, match="No ADRs stored"):
        _ = adrs.resolve_adr("latest")


def test_resolve_ref_list_prefers_adrs_then_recordings() -> None:
    """``uses``/``supersedes`` resolve against ADRs first, then recordings."""
    adr_refs = ["adr-2026-10-13T12-00-00.000000Z"]
    recording_refs = ["2026-10-13T12-00-00.000000Z", "2026-10-13T13-00-00.000000Z"]
    value = "adr-2026-10-13T12-00-00.000000Z 2026-10-13T13, nope"

    resolved = adrs.resolve_ref_list(value, adr_refs, recording_refs)

    assert [(item.raw, item.resolved) for item in resolved] == [
        ("adr-2026-10-13T12-00-00.000000Z", "adr-2026-10-13T12-00-00.000000Z"),
        ("2026-10-13T13", "2026-10-13T13-00-00.000000Z"),
        ("nope", None),
    ]


def test_resolve_ref_list_without_ambiguous_prefix() -> None:
    """A prefix that matches several refs stays unknown, never an error."""
    resolved = adrs.resolve_ref_list("2026", ["adr-1"], ["2026-a", "2026-b"])

    assert resolved == (adrs.ResolvedRef(raw="2026", resolved=None),)


# --- template --------------------------------------------------------------


def test_document_template_round_trips() -> None:
    """The edit template re-parses to the same document with new fields added."""
    document = adrs.create_adr({"title": "T", "decision": "D", "extra": "x"}, now=T0)

    template = adrs.document_template(document)
    parsed = adrs.parse_adr_document(template.encode("utf-8"), adrs.get_adrs_dir() / f"{document.ref}.toml")

    assert parsed.ref == document.ref
    assert parsed.created_at == document.created_at
    assert dict(parsed.fields) == dict(document.fields)


# --- doctor check ----------------------------------------------------------


def test_checks_missing_store_is_ok(tmp_path: Path) -> None:
    """A missing store is ``ok`` with an empty list and creates nothing."""
    findings = adrs.adr_checks(_ctx(tmp_path))

    assert len(findings) == 1
    assert findings[0].name == "adr.storage"
    assert findings[0].topic == "adr"
    assert findings[0].status == CheckStatus.OK
    assert "No ADRs" in findings[0].message
    assert not adrs.get_data_dir().exists()


def test_checks_present_store_is_ok(tmp_path: Path) -> None:
    """A store with documents reports the count."""
    _ = adrs.create_adr({"title": "T"}, now=T0)

    findings = adrs.adr_checks(_ctx(tmp_path))

    assert len(findings) == 1
    assert findings[0].status == CheckStatus.OK
    assert findings[0].message == "1 ADR(s) present."


def test_checks_broken_document_is_error(tmp_path: Path) -> None:
    """A broken document is an error naming its path, never skipped."""
    directory = adrs.get_adrs_dir()
    directory.mkdir(parents=True)
    path = directory / "adr-bad.toml"
    path.write_text("broken = [", encoding="utf-8")

    findings = adrs.adr_checks(_ctx(tmp_path))

    assert len(findings) == 1
    assert findings[0].name == "adr.adr-bad"
    assert findings[0].status == CheckStatus.ERROR
    assert str(path) in findings[0].message


def test_checks_unexpected_entry_is_error(tmp_path: Path) -> None:
    """An unexpected entry is an error without crashing the check."""
    directory = adrs.get_adrs_dir()
    directory.mkdir(parents=True)
    (directory / "notes.txt").write_text("x", encoding="utf-8")

    findings = adrs.adr_checks(_ctx(tmp_path))

    assert findings[0].status == CheckStatus.ERROR
    assert "Unexpected entry" in findings[0].message


def test_checks_non_directory_store_is_error(tmp_path: Path) -> None:
    """A store path that is not a directory is an error."""
    adrs.get_adrs_dir().parent.mkdir(parents=True)
    adrs.get_adrs_dir().write_text("x", encoding="utf-8")

    findings = adrs.adr_checks(_ctx(tmp_path))

    assert findings[0].status == CheckStatus.ERROR
    assert "not a directory" in findings[0].message


def test_checks_orphan_recording_is_warn(tmp_path: Path) -> None:
    """A recording reference without a matching recording is a warning."""
    _ = adrs.create_adr({"title": "T"}, recording_ref="does-not-exist", now=T0)

    findings = adrs.adr_checks(_ctx(tmp_path))

    assert len(findings) == 1
    assert findings[0].name.startswith("adr.adr-")
    assert findings[0].status == CheckStatus.WARN
    assert "does-not-exist" in findings[0].message


def test_checks_damaged_recording_space_does_not_crash(tmp_path: Path) -> None:
    """A damaged recording space skips orphan detection instead of failing."""
    _ = adrs.create_adr({"title": "T"}, recording_ref="maybe", now=T0)
    recordings = Path(os.environ["XDG_DATA_HOME"]) / "lion" / "recordings" / "2026-10-05T20-00-00.000000Z"
    recordings.mkdir(parents=True)
    (recordings / "meta.toml").write_text("broken = [", encoding="utf-8")

    findings = adrs.adr_checks(_ctx(tmp_path))

    assert [finding.status for finding in findings] == [CheckStatus.OK]
