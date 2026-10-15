# 0014 — Watch dog: an `accept-line` guard widget

- **Status:** accepted (E1 prototype, implemented in E5; see ADR 0020 for the
  shipped defaults and data flow)
- **Date:** 2026-10-12

## Context

`watch dog` is meant to ask for confirmation before critical input runs. It is
an honest guardrail, not a security boundary: it should catch a critical command
before execution and let the user confirm or back out, including when the input
arrives as a paste. It must not put a process on the key path, and it must not
interfere with the normal execution of non-critical input.

The E1 prototype proved the mechanism (`ignore/watch-prototype/`, Q7): a
rejection in an `accept-line` widget truly prevents execution — verified through
a pty side effect, for a single Enter **and** for a bracketed paste. `preexec`
cannot do this: by the time it runs, the command is already committed to
execution and cannot be aborted.

## Decision

The guard is a ZLE `accept-line` widget.

- Matching uses a Zsh ERE compiled from JSON rules; there is no Python in the
  key path and the guard applies to interactive input only.
- Confirmation is a **cancellable double-Enter**: the first Enter arms the
  guard and shows why, editing the buffer cancels, the second Enter executes.
- The widget calls the builtin `zle .accept-line` to execute, and its own
  function is installed as `accept-line`. A stable widget handle
  (`zle -A accept-line lion-dog-accept`) is published so other widgets can chain
  to the guard.
- Interop rule: a wrapper chains to the guard through a **widget name**
  (`zle lion-dog-accept`, or a `zle -A` copy of the previous `accept-line`),
  never by calling the guard's shell function directly with `zle` — calling the
  function directly does not chain and silently drops the confirmation.
- `watch dog on` / `off` / `status` toggles the guard independently of a
  running recording; the on/off state is persistent — the first real consumer of
  the config conventions (ADR 0009).
- Framing stays honest: the guard is documented as a guardrail, not sandboxing.

## Consequences

- A blocked command is truly not executed, including pasted input, which is what
  makes the guardrail meaningful rather than suggestive.
- The widget-handle rule fixes how the guard composes with other wrappers:
  every participant chains through widget names, never through the raw function.
- A JSON rule can only reject or prompt; it cannot run code, keeping the rule
  set declarative.
- Because the guard lives in the key path, its matching must be cheap and its
  rules validated loudly at load time.

## Alternatives

- **Guard in `preexec`.** Rejected: execution cannot be aborted from `preexec`;
  the prototype confirms the command still runs.
- **A Python process in the key path.** Rejected: a process per keystroke is far
  too slow, and it would couple the prompt to the runtime.
- **Do nothing on rejection (message only).** Rejected: a message that does not
  prevent execution is not a guardrail.
