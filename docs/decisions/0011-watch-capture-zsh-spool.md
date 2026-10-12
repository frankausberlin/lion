# 0011 — Watch capture architecture: a zsh-side spool

- **Status:** proposed (E1 draft; to be finalized with the E2 implementation)
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

## Alternatives

- **A `lion` process per input block.** Rejected: ~60 ms of process startup per
  event, measured, before any work; it would be felt in the prompt.
- **A single `zshaddhistory`-based capture (history only).** Rejected: history
  can drop lines (leading-space options, duplicates) and gives no exit status.
- **`accept-line` for capture.** Rejected: the key path must not be touched for
  capture; `accept-line` is reserved for the guard (ADR 0014).
- **Escaping via `$(…)`/external `printf`.** Rejected: a subshell per field,
  ~30× the cost of the parameter expansion.
