# 0013 — ADRs as flat annotation lines in recordings

- **Status:** accepted
- **Date:** 2026-10-12
- **Replaces:** the v0 draft of this number (block model with `begin`/`end` and a
  `#_LION:` prefix), which the E3 planning session dropped completely.

## Context

Installation guides want to attach *decisions* and *steps* to the input they
describe, so a recording can be read back as "why" and "what" rather than as a
raw command list. The marker has to live inside ordinary shell input, must not
change what the shell executes, and must survive the editing and quoting
realities of a terminal: aliases and wrappers wrap commands, pasted blocks
arrive as one buffer with embedded newlines, and text that merely *shows* a
marker (a string, a here-document body) must never become an annotation.

The E1 prototype (ADR 0011) confirmed the facts that shape the grammar:
`preexec` never fires for pure comment lines or empty lines (only
`zshaddhistory` sees them), pasted multi-line input arrives as one buffer, and
a marker token must be inert to the shell. `</>`-style markers fail that last
test — unquoted `</>` is redirection syntax — and the `#_LION:` prefix of the
v0 draft hides which block an annotation refers to and pairs `begin`/`end`
blocks that a lost line silently unbalances.

## Decision

**Annotations are flat comment lines. Three markers state the block they
attach to:**

- `#-LION:` — the *previous* block,
- `#+LION:` — the *following* block,
- `#.LION:` — the *most recently referenced* block (ditto).

A marker counts only as a **full comment line** outside strings and
here-documents, after optional leading whitespace, case-sensitive. A trailing
comment (`cmd #-LION: …`) is text; so is everything inside quotes or a
here-document body. With `interactive_comments` set (the documented
precondition), comment lines are inert input that `zshaddhistory` records; the
markers add no zsh-side machinery.

**The payload after the marker is either a typed line or (for `#.LION:`)
continuation prose:**

- `adr.<name> <value>` sets/overwrites one field; the catalog is `title`,
  `decision`, `component`, `scope`, `context`, `consequences`, `alternatives`,
  `revisit_when`, `uses`, `supersedes`, `status`, and arbitrary extra fields
  are allowed.
- `adr.<name>` without a value deletes that field (same at all three markers).
- `adr` without a value clears the whole entry.
- Text whose first token is neither `adr` nor `adr.<name>` continues the most
  recently **set** field — only at `#.LION:`; at `#-`/`#+` it is unresolved.
- The first token is the discriminator: a continuation line that starts with an
  `adr`-token is always read as a typed line (documented boundary).

**Reading folds, it does not block.** Annotation lines are folded over the
chronological event stream of the recording; setting, overwriting and deleting
apply in order and the last state wins — including `adr.title`, quietly. There
are no revisions, no pairing and no conflict display, so a missing line can
never unbalance an entry.

**At most one ADR per record**, and the record reference *is* the ADR id.
Several decisions become several recordings chained with `uses`/`supersedes`.
With no field left after the fold (including after `adr`) there is no ADR.

`complete` means `title` and `decision` are non-empty. `adr.status` is
`proposed` (default), `accepted` or `superseded`, independent of completeness
and of the implementation outcome; an unknown status value is kept and reported
as an issue.

**Derived metadata is never typed:** the time of the first/last annotation
line, the reading process's user, the host from `state-start.toml` (fallback
`Unknown`), the `cwd` of the first line, the record ref and the touched
segments. There is no `adr.id` and no invented origin.

**A small, deliberately bounded zsh mini-parser (Python) reads the lines.**
It understands quotes (`'`, `"`, `$'`), backslash escapes, here-documents
(`<<`/`<<-`, quoted or bare delimiters) and treats `$(`/backticks
conservatively: a construct left open at the end of a line makes following
marker-like lines `unresolved construct` issues instead of annotations.
Markers inside strings or here-document bodies are silent text. Findings are
visible but never fatal: segment, line in the event text, event time, masked
original line and a fixed reason (`empty annotation line`,
`unknown annotation form`, `adr takes no value`, `empty field name`,
`continuation without a field`, `unknown status value`,
`unresolved construct`).

**No new event writer:** ADRs are derived from the raw events on every read.
The only write that ever touches an ADR is the explicit `records edit` overlay
of milestone 2 (a separate decision, ADR 0012 addendum).

## Consequences

- The grammar stays usable while typing: every line is independently valid, an
  interrupted annotation sequence degrades to a smaller ADR, never to a broken
  state.
- The bounded parser prefers no annotation over a wrong one, consistent with
  the fail-loud posture of the rest of LION.
- `lion records status`, `list` and `adrs` read the recordings; a damaged
  record fails loudly with its path, and unreadable annotation input becomes a
  visible `Unresolved:` finding rather than a guess.
- `uses`/`supersedes` resolution happens at read time; unknown references show
  as `(unknown)` and are never errors.
- `step.*` directives do not exist. A step concept is deferred to E4/the
  classifier work.

## Alternatives

- **Block model with `begin`/`end` (the v0 draft).** Rejected: pairing is
  fragile on paste and on an interrupted session, and it buys nothing the fold
  does not already give.
- **The `#_LION:` prefix.** Rejected: it names an implementation rather than
  the attachment, offers no direction (previous/next/ditto), and reads like a
  variable name inside a comment.
- **`</>`-style markup tokens.** Rejected: a bare `</>` is shell redirection
  syntax, so it cannot live in unquoted input; quoting every marker would be
  error-prone and easy to get wrong.
- **A separate annotation file or sidecar.** Rejected: the annotation has to
  travel with the shell input to stay honest about what was actually run;
  `records edit` deliberately works on the record, not beside it (M2).
- **A full shell parser.** Rejected: too large, and the goal is a metadata
  marker, not shell execution semantics.
