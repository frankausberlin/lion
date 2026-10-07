# 0002 — `status` is read-only, `scan` writes

- **Status:** accepted
- **Date:** 2026-10-07

## Context

The state history only stays meaningful if it is clear which command changes it.
`status` is also the default command of the `lion` group, so it runs very often
and often by accident (for example in scripts or when a shell is opened).

## Decision

`status` is read-only: it collects the current state and compares it with the
latest stored one, and never writes. Writing into the history happens only in
`scan`, which decides between `created`, `confirmed` and `appended`. `history`
and `diff` are read-only too.

## Consequences

- Running `lion` or `lion status` can never change stored data, so it is safe to
  run anywhere, including in prompts and CI.
- Recording a state is an explicit act (`scan`), which keeps the history
  intentional and the timestamps trustworthy.
- An early planning appendix claimed `status` becomes the new last state; that
  description was wrong and is superseded by this decision.

## Alternatives

- Letting `status` also persist the comparison was rejected: it would blur the
  read/write split and make an implicit default command mutate the history.
