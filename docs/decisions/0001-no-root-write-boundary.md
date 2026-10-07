# 0001 — No root, writes confined to LION's own files

- **Status:** accepted
- **Date:** 2026-10-07

## Context

LION observes a Linux workstation: it collects machine facts and manages the
Zsh configuration. A tool that runs with elevated privileges or rewrites system
files can break a machine and is hard to trust. Users also need a clear,
predictable boundary for what LION may change.

## Decision

LION requires no root and modifies no system files. Every write is confined to
LION's own directories and the shell startup files it manages. Read-only
commands never write, and LION never repairs foreign files.

## Consequences

- LION is safe to run without `sudo` and cannot damage unrelated system state.
- Capabilities that would need root (installing packages, editing system
  configuration) are out of scope.
- The boundary is simple to state and to test: a write outside LION's own paths
  is a bug.

## Alternatives

- Running with elevated privileges to fix system state was rejected: it widens
  the blast radius and contradicts the observer role.
- A separate privileged helper was rejected as unnecessary complexity for the
  current scope.
