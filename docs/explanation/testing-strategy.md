# Testing strategy

LION is verified at three levels. Each covers something the others cannot, and
the higher levels never replace the lower ones. This page explains the levels,
defines **alpha testing**, and says why the disposable container cannot stand in
for a real machine. The operational checklists live in the
[E2E guide](../../tests/e2e/README.md); this page explains the why.

## The three levels

| Level | Command | Environment | What it proves |
| --- | --- | --- | --- |
| Unit and contract tests | `just check` | The developer's checkout, mocks and temporary paths | The observable behavior and the LION invariants of each module, deterministically and fast. |
| End-to-end tests | `just test-e2e` | A disposable, offline Ubuntu container | The installed `lion` executable against real tools and a real interactive zsh — watch lifecycle, exit and signal truthfulness, masking, races and an unprivileged user included — without touching the host. |
| Alpha test | manual | A real machine with the maintainer's own shell, prompt and hardware | The integration works in the environment LION actually runs in — the one thing the container cannot reproduce. |

`just check` is the gate every change must pass; it runs the fast suite, lint,
type checking and the documentation check. `just test-e2e` is opt-in and runs
only inside the disposable container, because it performs real package and shell
mutations. Both are automated and deterministic.

## What alpha testing is

An **alpha test** is a guided, manual pass on a real system. It has two parts:

- **Guided scenarios** — a short, repeatable checklist (one-minute and
  ten-minute variants) that exercises the watch lifecycle end to end and records
  the exact output.
- **Exploratory scenarios** — free probing beyond the checklist: odd annotation
  input, unusual pastes, real-config edge cases. The goal is to find what the
  scripted scenarios did not anticipate.

An alpha test is not a smoke test. A smoke test only shows that something starts
and does not crash; an alpha test deliberately uses the real environment and
looks for wrong behavior in it. The word "smoke" is therefore not used for this
manual phase.

## Why the container cannot substitute

The E2E container is deliberately minimal and isolated. That is what makes it
deterministic, and it is exactly why it cannot cover the real environment:

- **Offline.** Networking is disabled and no physical NIC exists, so the
  `network` collector sees only an empty list. Real interfaces never appear.
- **No real hardware.** The image installs no `lspci`, no `nvidia-smi` and no
  container engine, so GPU, tool-availability and container-engine findings are
  fixed by construction rather than observed.
- **A minimal Powerlevel10k.** The p10k scenario loads a committed, tiny config
  and a pinned Powerlevel10k, not the maintainer's real `~/.p10k.zsh` with its
  own segments, fonts and instant-prompt settings.
- **A terminal emulator, not a terminal.** The interactive scenarios drive zsh
  inside tmux so the screen is reproducible. That is a real terminal emulator,
  but not the maintainer's terminal, multiplexer or font.

These limits are intentional: they keep the automated suite honest and fast. The
properties they exclude are exactly the ones the alpha test exists to check.

## How the alpha test relates to the E2E suite

The two are complementary and share one rule: **results are read from the
recording and spool files, never from the terminal text.** The E2E watch
scenarios assert the observable behavior automatically; the alpha test repeats
the journey on real hardware and looks for anything the scripted assertions do
not cover. Run the alpha test after watch-related changes; it complements, and
never replaces, `just test-e2e`.

The interactive E2E scenarios run inside tmux (see
[tmux.conf](../../tests/e2e/tmux.conf) and
[watchutil.py](../../tests/e2e/watchutil.py)). That makes the prompt and screen
assertions deterministic and, as a side effect, lets a human watch a live run:

```bash
just test-e2e-attach
```

This variant keeps the container after the run and prints how to attach to the
running tmux session, so a test can be observed as it happens instead of only
through its saved files. The default `just test-e2e` stays headless and removes
the container, so it is safe for CI.

## Agentic QA (opt-in)

`just test-agent` runs an advisory LLM-as-tester suite (`tests/agentic/`,
marker `agentic`): a local Ollama model drives `lion serve` over stdio through
the real MCP client and answers eight scripted requests plus a nine-intent
tool-selection matrix. Hard assertions stay model-independent and scan the
whole call history — a required tool was called and the scenario check passed
on the fixture data, the ADR write path published exactly one valid document
and nothing else, no recorded secret reached the model-visible text, a server
error was delivered as an error. Tool selection, recovery, efficiency and answer quality are
reported per attempt and aggregated with explicit denominators in
`report.json`; they never fail the run.

The run is bounded end to end (steps, tool calls, chat and scenario timeouts,
measured teardown), every attempt is checkpointed atomically, and a preflight
failure still leaves a readable report. Transcripts and `report.json` land
under `agentic-artifacts.*/`; the suite identity, model digest and system
prompt hash make two reports comparable. The harness semantics are pinned
without a model by the ordinary `tests/test_agentic_harness.py`, which drives
the real scenarios through injected session and chat fakes.

It is deliberately **not** a fourth gate level: it needs a local model, and its
determinism depends on the model digest and the Ollama build (temperature 0
and a fixed seed reduce, but do not remove, variation). It is therefore never
part of `just check` or CI, and it never replaces the levels above — it
complements them on the one surface they cannot reach: how an external agent
actually calls the MCP tools.

## Where the rules live

The testing rules — determinism, mocks for external tools, the coverage floor
and when to run the E2E suite — are part of the agent contract in
[AGENTS.md](../../AGENTS.md#testing). The container's setup, isolation,
diagnostics and the alpha-testing checklists live in the
[E2E guide](../../tests/e2e/README.md).
