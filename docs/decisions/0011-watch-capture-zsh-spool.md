# 0011 — Watch capture architecture: a zsh-side spool

- **Status:** accepted (E2 implementation complete; see the implementation notes below)
- **Date:** 2026-10-12

## Context

`watch` must record shell input blocks persistently: a recording survives shell
restarts and system reboots, and the prompt stays responsive while it runs. The
input is read in zsh's interactive line editor, so the capture point is a small
set of zsh hooks. Two forces dominate the design: per-keystroke cost and the
fact that any failure in the capture path must never break the user's shell.

The E1 prototype measured the paths directly (`ignore/watch-prototype/`):

- a zsh hook that appends one escaped line costs about **70–90 µs** per event;
- a minimal `python3 -c pass` process costs ~8–9 ms per event;
- the real `lion --version` process (eager, no collection) costs ~60 ms, and a
  full `lion status` ~3.1 s.

Escaping the line with a command substitution (`$(...)`) instead of a parameter
expansion costs ~2.3 ms per event — a 30× trap. `preexec` also never sees pure
comment lines or empty lines (only `zshaddhistory` does), and its second
argument `$2` is size-limited and lossy (`cat <<EOF` becomes `cat <<<'abc'`),
so the typed line `$1` is the primary source, with `$3` as the fallback when
`$1` is empty (a line dropped from history).

## Decision

Capture happens entirely inside zsh; Python never runs in the input path.

- Install the hooks `preexec`, `precmd`, `zshaddhistory` and `zshexit`
  idempotently through `add-zsh-hook`.
- Append **one escaped event per line** to a per-shell spool file. Escaping uses
  the zsh parameter expansion `${(q)value}` — no command substitution, no
  subshell — and applies a size cap so one event cannot grow unbounded.
- Take the command text from `preexec`'s `$1`, falling back to `$3` when `$1`
  is empty; never use `$2` for extraction. Capture comment and empty lines
  through `zshaddhistory`, since `preexec` does not fire for them. Capture the
  exit status in `precmd`, where zsh restores `$?` before every hook.
- Mask secrets **in the hook** before the spool write, using a conservative zsh
  pattern list, with an explicit opt-out. This is best-effort and documented as
  such.
- Python normalizes the spool only on `watch start`, `watch stop` and
  `watch status`.

## Consequences

- The input path costs tens of microseconds per event instead of tens of
  milliseconds; a `lion` process per input block is off the table.
- The hook must stay dependency-free and defensive: a failure inside it must
  degrade to "no event", never to a broken prompt. Shell startup remains silent
  so Powerlevel10k's instant prompt is not disturbed.
- Masking is a convenience, not a security boundary: patterns can be fooled and
  the framework is documented honestly. The explicit opt-out is the escape
  hatch for workflows the masker cannot cover.
- Because `zshaddhistory` is also the path for comments, the annotation grammar
  (ADR 0013) has to define how it treats lines that `preexec` never sees.

## E2 implementation notes

- The delivered integration lives in `src/lion/program/shell.py` and is inserted
  by `lion shell insert`, either as `~/.shlib/shlibs/90-lion-watch.sh` (shlib
  mode) or as a marked block in `~/.zshrc`.
- Event grammar (one TAB-separated line per event, every field `${(q)}`-escaped):
  `segment`, `cmd`, `result`, `comment`, `exit`. The command text is `preexec`'s
  `$1` with `$3` as fallback; comments are captured through `zshaddhistory`
  (guarded by `interactive_comments`, empty input dropped). A `comment` event
  requires every non-blank line of the input to be a comment, so a pasted block
  that mixes comments and commands stays a single `cmd` and is never recorded
  twice; a comment-only multi-line paste is one `comment` event with embedded
  newlines. The exit status is captured in `precmd`, the shell exit in `zshexit`
  as a hint.
- The escape uses parameter expansion only (no command substitution). The raw
  text and the escaped field are each capped at 4096 characters via
  `LION_WATCH_CAP`; the decoder (`unquote_zsh`) treats an incomplete escape
  created by the cap as a literal. Its `$'...'` octal escapes are bytes, not
  codepoints: the decoder assembles the bytes and decodes them as UTF-8, so a
  command typed in a non-UTF-8 locale reads back correctly.
- The masker runs before the spool write with a local `extended_glob` and
  masks values of assignments whose name contains `TOKEN`, `SECRET`,
  `PASSWORD`, `PASSWD`, `API_KEY`, `APIKEY` or `CREDENTIAL` (bare, single- and
  double-quoted) plus `Authorization:` header values; `LION_WATCH_MASK=0` opts
  out. Masking is best-effort.
- The hooks read the `active` pointer on the first load and refresh it in
  `precmd`, so start/stop from any shell take effect at the next prompt. A
  repeated load (`source ~/.zshrc`) keeps the current ref, the open segment
  and a pending command, so the reload itself still gets its `result` event;
  the pointer is only read when the ref is still empty. Segment file and
  header are created lazily on the first event; a missing or stale pointer
  makes the hooks a no-op. Loading writes nothing to stdout (instant-prompt
  safe), and the p10k segment `prompt_lion_watch` only reads the pointer file.
- `zsh -n` validation and a timestamped `~/.zshrc` backup guard `insert`;
  exactly one artifact may exist and `remove` requires the marker line.

## Alternatives

- **A `lion` process per input block.** Rejected: ~60 ms of process startup per
  event, measured, before any work; it would be felt in the prompt.
- **A single `zshaddhistory`-based capture (history only).** Rejected: history
  can drop lines (leading-space options, duplicates) and gives no exit status.
- **`accept-line` for capture.** Rejected: the key path must not be touched for
  capture; `accept-line` is reserved for the guard (ADR 0014).
- **Escaping via `$(…)`/external `printf`.** Rejected: a subshell per field,
  ~30× the cost of the parameter expansion.
