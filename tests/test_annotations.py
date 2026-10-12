"""Tests for the annotation grammar in ``lion.program.annotations``.

Everything here is synthetic: events are built in memory, so the tests stay
independent of the filesystem, the developer's machine and the shell.
"""

from lion.program import annotations, recording


def _event(
    text: str,
    *,
    kind: str = "comment",
    time: float = 1.0,
    segment: str = "100-1-1.log",
    line: int = 1,
    cwd: str = "/tmp",
) -> recording.SpoolEvent:
    return recording.SpoolEvent(kind=kind, segment=segment, line=line, time=time, cwd=cwd, text=text)


def _fold(
    events: list[recording.SpoolEvent], host: str = "lion", user: str = "tester"
) -> annotations.RecordAnnotations:
    return annotations.fold_events(events, ref="2026-10-12T12-00-00.000000Z", host=host, user=user)


def _actions(events: list[recording.SpoolEvent]) -> list[annotations.AdrAction]:
    result: list[annotations.AdrAction] = []
    for event in events:
        result.extend(annotations.parse_event_text(event).actions)
    return result


def _issues(events: list[recording.SpoolEvent]) -> list[annotations.AdrIssue]:
    result: list[annotations.AdrIssue] = []
    for event in events:
        result.extend(annotations.parse_event_text(event).issues)
    return result


# --- marker detection --------------------------------------------------------


def test_marker_requires_full_comment_line() -> None:
    """Only a full comment line counts; case, position and trailing text matter."""
    assert _actions([_event("  #-LION: adr.title X", kind="cmd")])[0].kind == "set"
    assert _actions([_event("#+LION: adr.title X", kind="cmd")])[0].marker == "+"
    assert _actions([_event("#.LION: adr.title X", kind="cmd")])[0].marker == "."

    assert _actions([_event("#-lion: adr.title X")]) == []
    assert _actions([_event("#_LION: adr.title X")]) == []
    assert _actions([_event("echo hi #-LION: adr.title X", kind="cmd")]) == []
    assert _actions([_event("true; #-LION: adr.title X", kind="cmd")]) == []
    assert _issues([_event("echo hi #-LION: adr.title X", kind="cmd")]) == []


def test_non_adr_payload_on_previous_and_next_is_unresolved() -> None:
    """``#-``/``#+`` carry typed lines only; prose is an issue, not a continuation."""
    for line in ("#-LION: some prose", "#+LION: some prose"):
        issues = _issues([_event(line)])
        assert [issue.reason for issue in issues] == [annotations.REASON_UNKNOWN_FORM]
    # ``#.LION:`` prose parses as a continuation; without a target the fold
    # reports it (covered in the continuation tests).
    assert _issues([_event("#.LION: some prose")]) == []
    assert _fold([_event("#.LION: some prose")]).issues[0].reason == annotations.REASON_NO_TARGET


# --- payload forms -----------------------------------------------------------


def test_set_overwrite_delete_clear_and_last_wins() -> None:
    """Fields are set, overwritten and deleted in order; the last state wins."""
    events = [
        _event("#-LION: adr.title First"),
        _event("#.LION: adr.title Second"),
        _event("#.LION: adr.component shell"),
        _event("#.LION: adr.component"),
        _event("#-LION: adr.decision Keep"),
    ]
    result = _fold(events)

    assert result.adr is not None
    assert result.adr.fields == {"title": "Second", "decision": "Keep"}
    assert result.adr.complete is True

    cleared = _fold([*events, _event("#.LION: adr")])
    assert cleared.adr is None


def test_field_order_is_catalog_then_extra_fields() -> None:
    """Catalog fields come first; extras follow in first-set order."""
    events = [
        _event("#-LION: adr.zeta 1"),
        _event("#.LION: adr.title T"),
        _event("#.LION: adr.alpha 2"),
        _event("#.LION: adr.decision D"),
        _event("#.LION: adr.status accepted"),
    ]
    result = _fold(events)

    assert result.adr is not None
    assert list(result.adr.fields) == ["title", "decision", "status", "zeta", "alpha"]
    assert result.adr.complete is True
    assert result.adr.status == "accepted"


def test_complete_requires_title_and_decision() -> None:
    """Only title plus decision make an ADR complete."""
    title_only = _fold([_event("#-LION: adr.title T")])
    assert title_only.adr is not None
    assert title_only.adr.complete is False

    both = _fold([_event("#-LION: adr.title T"), _event("#.LION: adr.decision D")])
    assert both.adr is not None
    assert both.adr.complete is True

    deleted = _fold([_event("#-LION: adr.title T"), _event("#.LION: adr.decision D"), _event("#.LION: adr.decision")])
    assert deleted.adr is not None
    assert deleted.adr.complete is False


def test_continuation_appends_to_last_set_field() -> None:
    """``#.LION:`` prose extends the most recently set field."""
    events = [
        _event("#-LION: adr.title T"),
        _event("#-LION: adr.decision D"),
        _event("#.LION: line one"),
        _event("#.LION: line two"),
    ]
    result = _fold(events)

    assert result.adr is not None
    assert result.adr.fields["decision"] == "D\nline one\nline two"
    assert result.adr.fields["title"] == "T"
    assert result.issues == ()


def test_continuation_target_resets_on_delete_and_clear() -> None:
    """Deleting or clearing the target leaves no continuation target."""
    after_delete = _fold([_event("#-LION: adr.title T"), _event("#.LION: adr.title"), _event("#.LION: prose")])
    assert [issue.reason for issue in after_delete.issues] == [annotations.REASON_NO_TARGET]

    after_clear = _fold([_event("#-LION: adr.title T"), _event("#.LION: adr"), _event("#.LION: prose")])
    assert [issue.reason for issue in after_clear.issues] == [annotations.REASON_NO_TARGET]

    other_deleted = _fold(
        [
            _event("#-LION: adr.title T"),
            _event("#.LION: adr.decision D"),
            _event("#.LION: adr.title"),
            _event("#.LION: tail"),
        ]
    )
    assert other_deleted.adr is not None
    assert other_deleted.adr.fields["decision"] == "D\ntail"


def test_unknown_status_value_stays_and_is_reported() -> None:
    """An unknown status is an issue, but the value is kept verbatim."""
    result = _fold([_event("#-LION: adr.title T"), _event("#.LION: adr.status bogus")])

    assert result.adr is not None
    assert result.adr.status == "bogus"
    assert [issue.reason for issue in result.issues] == [annotations.REASON_UNKNOWN_STATUS]

    default = _fold([_event("#-LION: adr.title T")])
    assert default.adr is not None
    assert default.adr.status == "proposed"

    deleted = _fold(
        [_event("#-LION: adr.title T"), _event("#.LION: adr.status accepted"), _event("#.LION: adr.status")]
    )
    assert deleted.adr is not None
    assert deleted.adr.status == "proposed"


def test_issue_catalogue() -> None:
    """Every reason is reachable and reported with its source."""
    events = [
        _event("#-LION:"),
        _event("#-LION:    "),
        _event("#-LION:junk"),
        _event("#+LION: junk"),
        _event("#-LION: adr takes a value"),
        _event("#-LION: adr. x"),
    ]
    reasons = [issue.reason for issue in _issues(events)]

    assert reasons == [
        annotations.REASON_EMPTY_LINE,
        annotations.REASON_EMPTY_LINE,
        annotations.REASON_UNKNOWN_FORM,
        annotations.REASON_UNKNOWN_FORM,
        annotations.REASON_ADR_VALUE,
        annotations.REASON_EMPTY_FIELD,
    ]

    status_issue = _fold([_event("#-LION: adr.status bogus")]).issues
    assert [issue.reason for issue in status_issue] == [annotations.REASON_UNKNOWN_STATUS]


def test_issue_carries_source_information() -> None:
    """An issue names segment, line-in-event, time and the original line."""
    event = _event("first\n#-LION: adr. x", time=42.0, segment="200-2-2.log", line=7)
    issue = _issues([event])[0]

    assert issue.segment == "200-2-2.log"
    assert issue.line == 2
    assert issue.text == "#-LION: adr. x"
    assert issue.time.startswith("1970-01-01T00:00:42")


# --- parser limits -----------------------------------------------------------


def test_markers_inside_quotes_are_text() -> None:
    """Quoted marker-like lines are silent, never findings."""
    for text in (
        'echo "#-LION: adr.title X"',
        "echo '#+LION: adr.title X'",
        "echo $'#.LION: adr.title X'",
    ):
        assert _actions([_event(text, kind="cmd")]) == []
        assert _issues([_event(text, kind="cmd")]) == []


def test_markers_inside_here_documents_are_text() -> None:
    """Here-document bodies stay text; the code after the terminator is scanned."""
    text = "cat <<EOF\n#-LION: adr.title FAKE\nEOF\n#-LION: adr.title Real"
    actions = _actions([_event(text, kind="cmd")])

    assert [(action.kind, action.name, action.value) for action in actions] == [("set", "title", "Real")]
    assert _issues([_event(text, kind="cmd")]) == []


def test_here_document_variants() -> None:
    """``<<-`` strips tabs and quoted delimiters match literally."""
    tabbed = "cat <<-EOF\n\t#-LION: adr.title FAKE\n\tEOF\n#-LION: adr.title Real"
    assert [action.value for action in _actions([_event(tabbed, kind="cmd")])] == ["Real"]

    quoted = "cat <<'EOF'\n#-LION: adr.title FAKE\nEOF\n#-LION: adr.title Real"
    assert [action.value for action in _actions([_event(quoted, kind="cmd")])] == ["Real"]

    here_string = "cat <<< word\n#-LION: adr.title Real"
    assert [action.value for action in _actions([_event(here_string, kind="cmd")])] == ["Real"]


def test_escaped_quote_does_not_open_a_string() -> None:
    """A backslash-escaped quote is inert, so the next line is scanned again."""
    text = "echo don\\'t stop\n#-LION: adr.title Real"
    assert [action.value for action in _actions([_event(text, kind="cmd")])] == ["Real"]


def test_unresolved_construct_reports_marker_like_lines() -> None:
    """An open ``$(`` or backtick makes following marker lines unresolved."""
    substitution = "echo $(date\n#-LION: adr.title X"
    issues = _issues([_event(substitution, kind="cmd")])
    assert [issue.reason for issue in issues] == [annotations.REASON_UNRESOLVED]
    assert _actions([_event(substitution, kind="cmd")]) == []

    backtick = "echo `date\n#-LION: adr.title X"
    issues = _issues([_event(backtick, kind="cmd")])
    assert [issue.reason for issue in issues] == [annotations.REASON_UNRESOLVED]

    resolved = "echo $(date +%s)\n#-LION: adr.title Real"
    assert [action.value for action in _actions([_event(resolved, kind="cmd")])] == ["Real"]
    assert _issues([_event(resolved, kind="cmd")]) == []


def test_dollar_single_quote_continues_across_lines() -> None:
    """An open ``$'...'`` is a cross-line quote state, not an unknown construct."""
    text = "echo $'first\n#-LION: adr.title Hidden\n'\n#-LION: adr.title Real"
    actions = _actions([_event(text, kind="cmd")])

    assert [(action.kind, action.name, action.value) for action in actions] == [("set", "title", "Real")]
    assert _issues([_event(text, kind="cmd")]) == []


def test_backslashed_here_document_delimiter_matches_the_word() -> None:
    r"""``<<\EOF`` closes on ``EOF`` like the shell, not on a one-char delimiter."""
    text = "cat <<\\EOF\n#-LION: adr.title FAKE\nEOF\n#-LION: adr.title Real"
    actions = _actions([_event(text, kind="cmd")])

    assert [action.value for action in actions] == ["Real"]
    assert _issues([_event(text, kind="cmd")]) == []


def test_anchor_fixed_by_the_first_marker_without_neighbour() -> None:
    """A later ``#+`` cannot attach an anchor the first marker left without."""
    events = [
        _event("#-LION: adr.title T", time=1.0),
        _event("echo A", kind="cmd", time=2.0, line=2),
        _event("#+LION: adr.decision D", time=3.0),
        _event("echo B", kind="cmd", time=4.0, line=4),
    ]
    result = _fold(events)

    assert result.adr is not None
    assert result.adr.anchor is None


def test_comment_events_do_not_carry_quote_state() -> None:
    """A comment event is all comments, so quotes inside never hide a marker."""
    comment = _event("# it's fine\n#-LION: adr.title Real", kind="comment")
    assert [action.value for action in _actions([comment])] == ["Real"]

    unterminated = _event("echo 'unterminated\n#-LION: adr.title Hidden", kind="cmd")
    assert _actions([unterminated]) == []


# --- fold metadata -----------------------------------------------------------


def test_auto_fields_and_segments_across_events() -> None:
    """First/last marker times, cwd and touched segments come from the events."""
    events = [
        _event("echo A", kind="cmd", time=1.0, segment="100-1-1.log", line=2, cwd="/first"),
        _event("#-LION: adr.title T", time=2.0, segment="100-1-1.log", line=3, cwd="/first"),
        _event("#.LION: adr.decision D", time=3.0, segment="200-2-2.log", line=4, cwd="/second"),
    ]
    result = _fold(events, host="myhost", user="reader")

    assert result.adr is not None
    assert result.adr.first_at.startswith("1970-01-01T00:00:02")
    assert result.adr.last_at.startswith("1970-01-01T00:00:03")
    assert result.adr.cwd == "/first"
    assert result.adr.segments == ("100-1-1.log", "200-2-2.log")
    assert result.adr.host == "myhost"
    assert result.adr.user == "reader"
    assert result.adr.edited is False


def test_anchor_derivation() -> None:
    """The first ``#-``/``#+`` line fixes the anchor; ``#.`` inherits it."""
    events = [
        _event("echo A", kind="cmd", time=1.0, line=2),
        _event("#+LION: adr.title T", time=2.0, line=3),
        _event("echo B", kind="cmd", time=3.0, line=4),
        _event("#-LION: adr.decision D", time=4.0, line=5),
        _event("#.LION: tail", time=5.0, line=6),
    ]
    result = _fold(events)

    assert result.adr is not None
    assert result.adr.anchor is not None
    assert result.adr.anchor.kind == "next"
    assert result.adr.anchor.text == "echo B"
    assert result.adr.anchor.line == 4


def test_anchor_for_previous_and_missing_neighbours() -> None:
    """``#-`` takes the previous command; without a neighbour the anchor is none."""
    previous = _fold([_event("echo A", kind="cmd", time=1.0, line=2), _event("#-LION: adr.title T", time=2.0)])
    assert previous.adr is not None
    assert previous.adr.anchor is not None
    assert previous.adr.anchor.kind == "previous"
    assert previous.adr.anchor.text == "echo A"

    no_previous = _fold([_event("#-LION: adr.title T")])
    assert no_previous.adr is not None
    assert no_previous.adr.anchor is None

    no_next = _fold([_event("#+LION: adr.title T")])
    assert no_next.adr is not None
    assert no_next.adr.anchor is None

    ditto_only = _fold([_event("#.LION: adr.title T")])
    assert ditto_only.adr is not None
    assert ditto_only.adr.anchor is None


def test_no_field_means_no_adr() -> None:
    """Issues alone or a cleared entry yield no ADR, but keep the issues."""
    issues_only = _fold([_event("#-LION: junk")])
    assert issues_only.adr is None
    assert len(issues_only.issues) == 1

    cleared = _fold([_event("#-LION: adr.title T"), _event("#.LION: adr")])
    assert cleared.adr is None
    assert cleared.issues == ()


def test_fold_is_deterministic() -> None:
    """Folding the same events twice yields identical results."""
    events = [
        _event("echo A", kind="cmd", time=1.0, line=2),
        _event("#-LION: adr.title T", time=2.0),
        _event("#.LION: adr.decision D", time=3.0),
        _event("#.LION: tail", time=4.0),
        _event("#.LION: adr.status bogus", time=5.0),
    ]
    first = _fold(events)
    second = _fold(events)

    assert first == second


# --- reference resolution ----------------------------------------------------


def test_parse_ref_list_splits_on_whitespace_and_commas() -> None:
    """Tokens are separated by whitespace and/or commas."""
    assert annotations.parse_ref_list("a, b  c,d") == ("a", "b", "c", "d")
    assert annotations.parse_ref_list("  ") == ()


def test_resolve_ref_list_matches_exact_and_unique_prefixes() -> None:
    """Resolution is case-insensitive; unknown or ambiguous tokens stay ``None``."""
    refs = ["2026-10-12T09-00-00.000000Z", "2026-10-13T10-00-00.000000Z", "2026-10-1x"]
    resolved = annotations.resolve_ref_list(
        "2026-10-12T09-00-00.000000Z, 2026-10-13, nope 2026-10-1",
        refs,
    )

    assert [(item.raw, item.resolved) for item in resolved] == [
        ("2026-10-12T09-00-00.000000Z", "2026-10-12T09-00-00.000000Z"),
        ("2026-10-13", "2026-10-13T10-00-00.000000Z"),
        ("nope", None),
        ("2026-10-1", None),
    ]
