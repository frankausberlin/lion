# Agentic QA (opt-in)

This suite is an advisory, opt-in complement to the automated levels: a local
LLM drives the real `lion serve` MCP server over stdio and answers scripted
requests. It never runs in `just check` or CI — it needs a local Ollama model,
and its determinism depends on the model digest and the Ollama build. General
testing rules live in [AGENTS.md](../../AGENTS.md#testing); the level model and
why this is not a fourth gate live in
[docs/explanation/testing-strategy.md](../../docs/explanation/testing-strategy.md).

## Prerequisites and execution

- A running Ollama instance (`ollama serve`, default `http://127.0.0.1:11434`).
- A tool-capable model, by default
  `JetBrains/mellum2-instruct-q4_k_m:latest` (12.1B, Q4_K_M, ~8.1 GB;
  `ollama pull JetBrains/mellum2-instruct-q4_k_m:latest`). On an 8 GB GPU Ollama
  offloads part of the model to the CPU; that costs speed, not determinism.

```bash
just test-agent
```

The recipe runs `uv run pytest tests/agentic -m agentic -v` without coverage.
The `agentic` marker is excluded by `addopts`, so ordinary `pytest`, `just test`
and `just check` never collect a running test from here. A missing Ollama
service or model is a loud setup error with an action hint (start the service,
pull the model) — never a skip, because the suite is explicitly opt-in.

## How it works

Every scenario starts a real `lion serve` subprocess (the installed console
script) and talks to it through the official MCP SDK client, exactly like an
external agent would. The harness reads the tool inventory from `tools/list`,
converts it to the Ollama function format, and loops: send the conversation,
execute the model's tool calls against the live server, feed the results (JSON
or error text) back, at most 8 steps and 300 s per scenario. One model call
times out after 120 s.

One source of truth for sampling is `tests/agentic/agent.py`:

| Option | Value | Note |
| --- | --- | --- |
| `temperature` | `0` | greedy decoding |
| `seed` | `42` | `LION_AGENT_SEED` overrides |
| `top_k` / `top_p` | `1` / `1.0` | no sampling spread |
| `repeat_penalty` | `1.0` | neutral |
| `num_ctx` | `16384` | `LION_AGENT_NUM_CTX` overrides (lower it under VRAM pressure) |
| `num_predict` | `1024` | enough for tool-call turns |
| `keep_alive` | `30m` | keeps the model resident across scenarios |

Environment overrides for experiments: `LION_AGENT_MODEL`,
`LION_AGENT_OLLAMA_HOST`, `LION_AGENT_SEED`, `LION_AGENT_NUM_CTX`. The model
digest and the resolved options are recorded in `report.json`; there is no
pinning.

## Scenarios

| # | Scenario | Hard assertion | Reported |
| --- | --- | --- | --- |
| 1 | History overview (two states seeded) | `lion_history` or `lion_snapshot` called and a result delivered | answer quality |
| 2 | Compare the two newest states | `lion_diff` resolves A → B, `changed` is true | argument choice |
| 3 | `review_latest_changes` prompt as the user turn | `lion_diff` resolves A → B, `changed` is true | — |
| 4 | Search for a unique token | `lion_search` called, the token appears in the result | hit list |
| 5 | ADR write path | `lion_adr_add` called; file exists, mode `600`, `lion adr show` reads fields back; no other ledger writes | field completeness |
| 6 | Recording requested, none stored | `lion_recording`/`lion_recordings` called and the error is delivered | final answer claims no success |
| 7 | Redaction with a recorded secret | secret absent from every transcript entry, `***` in the tool result | `LION_SERVE_REDACT=0` variant shows the difference |
| 8 | Tool-selection matrix (9 read intents) | — (report only) | first-tool hit ratio |

Hard assertions are model-independent. Everything in the "Reported" column and
the matrix ratio are written to `report.json` without failing the run: they are
measurements of the model, not of LION.

## Artifacts

Each run creates `agentic-artifacts.XXXXXX/` in the repository root (gitignored,
removed by `just clean`):

| Artifact | Purpose |
| --- | --- |
| `report.json` | Model name, digest, capabilities, options, per-scenario outcomes (calls, durations, token counts, final text) and the tool-selection ratio. |
| `transcripts/<scenario>.jsonl` | Every model-visible message: system prompt, user turn, assistant replies with tool calls, tool results (including errors) and the final answer. The redaction scenario asserts on these files. |

## Determinism limits

`temperature 0` with a fixed seed is approximately deterministic only for the
same model digest and the same Ollama build; a different digest, quantization
or server version can change the output. Partial CPU offload changes speed,
not the sampling. Treat every result as advisory: a failing hard assertion is a
signal to look at the transcript, and the matrix ratio is a trend line, not a
gate. Run it when changing the MCP server, its prompts, its redaction or the
tool inventory — not on every change.
