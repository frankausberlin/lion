# Records and ADRs

> Referenced by [ADR 0013](../decisions/0013-watch-adr-annotations.md).

`lion watch` records raw shell input; `#-LION:` / `#+LION:` / `#.LION:`
comment lines inside that input are read back as **one ADR** (architecture
decision record) per recording. This page documents the grammar, the fold and
the `lion records` views. Command usage lives in `lion records --help` and
[Record a shell session](../how-to/record-shell.md).

## Markers

| Marker | Attaches to |
| --- | --- |
| `#-LION:` | the previous block |
| `#+LION:` | the following block |
| `#.LION:` | the most recently referenced block (ditto) |

A marker counts only as a **full comment line** outside strings and
here-documents, after optional leading whitespace, and it is case-sensitive.
`cmd #-LION: …` (trailing comment), `echo "#-LION: …"` (string) and a marker
inside a here-document body are text, not annotations. The old `#_LION:`
prefix is an ordinary comment.

With `interactive_comments` set, a comment line is never executed; the watch
integration records it as a `comment` event (or inside a mixed paste as part
of one `cmd` event). Without the option, `#` lines run as commands and the
marker is still read from the recorded text — but the comments are no longer
inert input, so setting `interactive_comments` remains the documented
precondition of the workflow.

## Payload

```zsh
#+LION: adr.title Antidote and Powerlevel10k for the interactive shell
#+LION: adr.component shell
git clone --depth=1 https://github.com/mattmc3/antidote.git ~/.antidote
#-LION: adr.decision Load plugins through Antidote and use Powerlevel10k.
#.LION: Note: the red watch dot stays visible in the prompt.
#.LION: adr.consequences Additional plugins and configuration to maintain.
#.LION: adr.status accepted
#.LION: adr.alternatives Default prompt — does not provide the status segments.
#.LION: adr.alternatives      ← deletes the field again (empty value)
#.LION: adr.title             ← deletes the title (entry becomes incomplete)
```

| Payload | Effect |
| --- | --- |
| `adr.<name> <value>` | sets/overwrites the field; any name is allowed |
| `adr.<name>` | deletes the field (same at all three markers) |
| `adr` | clears the whole entry (no field ⇒ no ADR) |
| other text | continuation of the last **set** field — only at `#.LION:` |

The first token decides: `adr` or `adr.<name>` is a typed line; a continuation
line that starts with an `adr` token is read as a typed line (documented
boundary). During the fold the last state wins — repeated fields, including
`adr.title`, are overwritten quietly; there is no revision or conflict view.

## Fold and metadata

All annotation lines of a recording (every segment, chronological order, as
`lion watch show` orders events) fold into **at most one ADR**; the record
reference is the ADR id. Several decisions are several recordings, chained via
`uses`/`supersedes`.

The **catalog** order is `title`, `decision`, `component`, `scope`, `context`,
`consequences`, `alternatives`, `revisit_when`, `uses`, `supersedes`,
`status`; extra fields follow in the order they were first set. `title` and
`decision` together make the ADR `complete`; `status` defaults to `proposed`
and also accepts `accepted` and `superseded`. An unknown status value is
reported as an issue and kept verbatim.

The **auto fields** are never typed: `first_at`/`last_at` (first/last
annotation line), `user` (the reading process), `host` (from the record's
`state-start.toml`, `Unknown` when unavailable), `cwd` (first annotation
line), the record `ref` and the touched `segments`. The `anchor` is the
command block of the first `#-`/`#+` line: the nearest previous (respectively
next) `cmd` event.

## Findings

Unreadable annotation input is never guessed; it becomes a visible
`Unresolved:` finding with segment, line in the event text, time, masked
original text and one of these reasons:

| Reason | Trigger |
| --- | --- |
| `empty annotation line` | marker without payload (`#-LION:`) |
| `unknown annotation form` | non-`adr` payload at `#-`/`#+`, or missing space after the colon |
| `adr takes no value` | `adr` followed by a value |
| `empty field name` | `adr.` without a name |
| `continuation without a field` | `#.LION:` prose before any field, or after its deletion/clear |
| `unknown status value` | `adr.status` outside `proposed`/`accepted`/`superseded` |
| `unresolved construct` | marker-like line inside an open `$(`/backtick or an unparsable operator |

## Parser limits

The reader is a small, deliberately bounded zsh mini-parser (Python): quotes
(`'`, `"`, `$'`), backslash escapes, here-documents (`<<`/`<<-`, quoted or
bare delimiters) and `$(`/backticks. Markers inside strings and here-document
bodies are text; an open `$(`, an open backtick or an unparsable redirection
makes following marker-like lines `unresolved construct` findings until the
end of the event. Known boundaries, accepted on purpose:

- A here-document inside a command substitution is outside the model; the
  parser stays conservative instead of guessing.
- The masker runs before the spool line is written, so annotations are read
  from masked text; secrets appear as `***` in ADR fields too.

## `lion records`

| Command | Behavior |
| --- | --- |
| `records` / `records status` | Overview: active recording or none, recordings and open count, ADR counters (`total`, `complete`, `incomplete`, `edited`, status distribution). Read-only, never collects. |
| `records list` | Like `lion watch list` plus an `ADR` column: `-`, `incomplete`, `proposed`, `accepted`, `superseded` (or the unknown value). |
| `records adrs [REF]` | Without `REF`: all ADRs oldest first (`#`, REF, STATUS, TITLE, STARTED). With `REF`: one ADR in detail. |

The detail view prints the header `ADR <ref> (<status>, complete|incomplete)`,
the fields in catalog order, the anchor with command preview, the auto fields,
`uses`/`supersedes` resolved against the recordings (`(unknown)` when there is
no match) and an `Unresolved:` section when there are findings. A record
without an ADR is a message with exit code 0, not an error; a damaged record
fails loudly with its path and exit code 1.

`REF` resolves like everywhere in LION: exact reference, unique prefix,
`latest` and — while a recording is open — `active`.

### JSON shapes

`records status --json`:

```json
{"active": {"ref": "…", "started_at": "…", "note": "…", "segments": 1} | null,
 "recordings": 2, "open": 0,
 "adrs": {"total": 1, "complete": 1, "incomplete": 0, "edited": 0,
           "status": {"proposed": 0, "accepted": 1, "superseded": 0}}}
```

`records list --json` keeps the `lion watch list` recording objects and adds
`"adr": {"status", "complete", "edited", "title"}` or `null`.

`records adrs --json`: `{"adrs": [{"ref", "status", "complete", "edited",
"title", "started_at"}]}`.

`records adrs REF --json`:

```json
{"adr": {"ref": "…", "fields": {"title": "…"}, "status": "accepted",
          "complete": true, "edited": false,
          "anchor": {"kind": "previous", "segment": "…", "line": 2, "time": "…", "text": "…"} | null,
          "auto": {"first_at": "…", "last_at": "…", "user": "…", "host": "…", "cwd": "…", "segments": ["…"]},
          "uses": [{"ref": "…", "resolved": "…"}], "supersedes": [],
          "issues": [{"reason": "…", "segment": "…", "line": 1, "time": "…", "text": "…"}]}}
```

A record without an ADR yields `{"adr": null, "issues": [...]}`. The read
commands never write and never touch the scan history; the JSON of `scan`,
`status` and `watch` is unchanged.
