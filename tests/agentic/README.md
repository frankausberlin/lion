# Agentic QA (opt-in)

This suite is an advisory, opt-in complement to the automated levels: a model
behind a configured provider drives the real `lion serve` MCP server over
stdio and answers scripted requests. It never runs in `just check` or CI — it
needs a live provider, and its determinism depends on the model digest and the
provider build. General testing rules live in
[AGENTS.md](../../AGENTS.md#testing); the level model and why this is not a
fourth gate live in
[docs/explanation/testing-strategy.md](../../docs/explanation/testing-strategy.md).
The harness semantics themselves (configuration, report contract, predicates,
recovery metrics, budget terminations, message rendering, provider preflight,
metadata, evaluation report) are pinned without a model by
[tests/test_agentic_harness.py](../test_agentic_harness.py) and the
configuration tests in
[tests/test_agentic_config.py](../test_agentic_config.py).

## Configuration

The provider is configured in the committed
[`agentic.toml`](agentic.toml) (schema 1):

```toml
schema_version = 1

[provider]
name = "openrouter"                       # "openrouter" | "ollama"
model = "deepseek/deepseek-v4.1-flash"    # OpenRouter model (paid; avoids free-pool limits)
base_url = "https://openrouter.ai/api/v1" # OpenRouter only
# host = "http://127.0.0.1:11434"         # Ollama only (default)

[report]
enabled = true
language = "english"                      # "english" | "german"
```

Precedence per field is builtin default < file < environment. A missing file
means the builtin defaults; a present but unreadable, malformed or wrongly
typed file is a loud error naming the path — an experiment never partially
applies a broken config. The environment overrides are `LION_AGENT_PROVIDER`,
`LION_AGENT_MODEL`, `LION_AGENT_BASE_URL` (OpenRouter), `LION_AGENT_OLLAMA_HOST`
(Ollama), `LION_AGENT_REPORT_LANGUAGE`, `LION_AGENT_SEED`,
`LION_AGENT_NUM_CTX` and `LION_AGENT_SYSTEM_PROMPT_FILE`. The provider's
unused endpoint key is ignored. The OpenRouter API key is read from
`OPENROUTER_API_KEY` only and never reaches a report, a transcript or an
extras value.

## Prerequisites and execution

OpenRouter (default):

- An OpenRouter API key: `export OPENROUTER_API_KEY=...`. The preflight checks
  the key against `GET /key` (an HTTP 401/403 is a loud key error) and then
  `GET /models`: the configured model must be listed and
  `tools` must appear in its `supported_parameters`. A model entry without a
  parameter list means unknown capabilities and is accepted (some entries,
  including free-routed ones, may not enumerate them).

Ollama (opt-in):

- Switch the provider in `tests/agentic/agentic.toml`
  (`name = "ollama"`) and set a local model, for example
  `model = "JetBrains/mellum2-instruct-q4_k_m:latest"` (12.1B, Q4_K_M,
  ~8.1 GB; `ollama pull JetBrains/mellum2-instruct-q4_k_m:latest`), plus a
  running Ollama instance (`ollama serve`, default `http://127.0.0.1:11434`).
  On an 8 GB GPU Ollama offloads part of the model to the CPU; that costs
  speed, not determinism.

```bash
just test-agent
```

The recipe runs `uv run pytest tests/agentic -m agentic -v -s` without
coverage (the `-s` shows the evaluation-report path and the tool-selection
line). The `agentic` marker is excluded by `addopts`, so ordinary `pytest`,
`just test` and `just check` never collect a running test from here. A missing
credential, an unreachable or rejected provider, or a missing model is a loud
setup error with an action hint — never a skip, because the suite is
explicitly opt-in. The failure still writes `report.json` with
`state = "preflight_error"`, so the artifact always exists.

Free routing (`openrouter/free` or a `:free` model) goes through a shared,
rate-limited pool (HTTP 429 with `limit_source: upstream_provider_shared_pool`):
a transient 429 lands as a recorded `transport_error` and fails its hard
scenario, so a run on a rate-limited pool needs a re-run or a model with its
own limits. The default model runs outside the free pool; the evaluation
report attributes such errors to the upstream provider.

## How it works

Every scenario starts a real `lion serve` subprocess (the installed console
script) and talks to it through the official MCP SDK client, exactly like an
external agent would. The harness reads the tool inventory from `tools/list`,
converts it to the OpenAI function format, and loops: send the conversation,
execute the model's tool calls against the live server, feed the results (JSON
or error text) back. One non-streamed request goes to Ollama (`/api/chat`) or
OpenRouter (`/chat/completions`). The execution budget is end to end (the
values mirror the constants in `tests/agentic/agent.py`, the source of truth):

| Limit | Value | Termination reason |
| --- | --- | --- |
| Model steps per scenario | `8` | `step_limit` |
| Tool calls per model turn | `4` | `call_limit` |
| Tool calls per scenario | `16` | `call_limit` |
| One model call | `120 s` | `chat_timeout` |
| Whole scenario | `300 s` | `scenario_timeout` |
| One evaluation call | `240 s` | recorded, never raised |
| Server teardown after a timeout | `15 s` (measured) | — |

The chat request uses httpx with the remaining scenario budget, so a timeout is
a real cancellation; the MCP SDK's shielded shutdown then reaps the `lion serve`
subprocess inside the cleanup allowance, and each attempt records the measured
teardown duration. Transport errors, unparseable responses, malformed tool
arguments, missing server start and harness-internal exceptions map to the
closed termination vocabulary (`transport_error`, `malformed_output`,
`startup_error`, `harness_error`).

The internal messages carry the union of both provider shapes and are rendered
exactly once per request (`render_messages`). Ollama receives them unchanged,
`thinking` included, with `tool_name` on tool results. OpenRouter receives the
OpenAI shape: assistant tool calls with `id` and `type: "function"` (a missing
`id` is synthesized as `call_<n>`), `thinking` dropped, tool results as
`{"role": "tool", "tool_call_id": ..., "content": ...}` without `tool_name`
(a tool result without an id is paired with the preceding assistant calls in
order). Assistant tool-call arguments are sent as the JSON string the API
expects: a non-string value is `json.dumps`-ed, a missing or `None` value
becomes `"{}"`, and an existing string stays untouched. The transcript records
exactly the rendered request.

Every attempt is registered in `report.json` before it starts (the `pending`
field of the checkpoint) and the report is checkpointed atomically after it —
exactly one outcome (`passed`/`failed`/`error`) per attempt, with the partial
data (calls, steps, token counts) on failures. An interrupted run stays
readable (`state = "interrupted"`), and hard scenarios re-raise their failure
only after the recording.

One source of truth for sampling is `tests/agentic/agent.py`:

| Option | Value | Note |
| --- | --- | --- |
| `temperature` | `0` | greedy decoding |
| `seed` | `42` | `LION_AGENT_SEED` overrides |
| `top_k` / `top_p` | `1` / `1.0` | no sampling spread; `top_k` is Ollama-only |
| `repeat_penalty` | `1.0` | neutral; Ollama-only |
| `num_ctx` | `16384` | `LION_AGENT_NUM_CTX` overrides; Ollama-only |
| `num_predict` | `1024` | sent as `max_tokens` on OpenRouter; enough for tool-call turns |
| `keep_alive` | `30m` | Ollama-only; keeps the model resident across scenarios |
| `think` | not sent | the report records `effective: "model default"` |

The system prompt is the builtin constant unless
`LION_AGENT_SYSTEM_PROMPT_FILE` points at a readable, non-empty file; an
unreadable or empty variant is a loud error, and the report pins its source,
text and SHA-256. There is no pinning of the model. The report also pins the
resolved `endpoint` and the system-prompt text, so keep credentials out of
`LION_AGENT_BASE_URL`, `LION_AGENT_OLLAMA_HOST` and the prompt override file.

## Scenarios

| # | Scenario | Hard assertion | Reported |
| --- | --- | --- | --- |
| 1 | History overview (two states seeded) | a successful `lion_history` result lists both fixture refs, or a successful `lion_snapshot` result is fixture state B | answer quality |
| 2 | Compare the two newest states | a successful `lion_diff` resolves A → B, `changed` is true | argument choice |
| 3 | `review_latest_changes` prompt as the user turn | same A → B diff assertion | — |
| 4 | Search for a unique token | a successful `lion_search` result contains the token | hit list |
| 5 | ADR write path | exactly one published ADR: file exists, mode `600`, `title` and `decision` complete, the decision names `stdio`, `lion adr show` reads it back, no other ledger writes | field completeness |
| 6 | Recording requested, none stored | `lion_recording` was called and the missing-recording error was delivered as an error result | final answer claims no success |
| 7 | Redaction with a recorded secret | `***` in the tool result and the secret absent from every transcript entry | `LION_SERVE_REDACT=0` variant is report-only and shows the raw secret |
| 8 | Tool-selection matrix (9 read intents) | — (report only; the first-tool expectation is the check, so a miss is a recorded `failed`) | first-tool hit ratio |

Hard assertions are model-independent and always scan the whole call history: a
failed call followed by a successful retry counts as recovery, and a successful
but wrong result (wrong direction, wrong reference, `changed: false`, missing
fields, a second ADR) never passes.

## Artifacts

Each run creates `agentic-artifacts.XXXXXX/` in the repository root (gitignored,
removed by `just clean`):

| Artifact | Purpose |
| --- | --- |
| `report.json` | Run metadata, per-attempt outcomes and metrics, aggregate metrics, extras. |
| `agentic-report-<run-id>.md` | The model-written evaluation of the run (one bounded, tool-less chat call; see below). |
| `transcripts/<scenario>.jsonl` | Every model-visible message: tool inventory hash, system prompt, user turn, each `chat_request` with the rendered messages as sent (plus the normalization hint), assistant replies with raw `tool_calls`, `thinking`, `done_reason` and the routed `model`, tool results with the called `name`, `tool_call_id` and durations, and the final answer. The redaction scenario asserts on these files. |

### Reading `report.json`

`schema_version` is `2`. The top level carries `run` (`id`, `started_at`,
`finished_at`, `state`: `running`/`complete`/`interrupted`/`preflight_error`),
`provider` (`openrouter`/`ollama`), `endpoint`, `model` (name, digest,
capabilities; the OpenRouter digest is empty because the router may route each
request to a different free model), `options` (the resolved sampling options;
the provider-specific wire mapping applies, so for example OpenRouter receives
only `temperature`/`top_p`/`seed`/`max_tokens` while `top_k`, `repeat_penalty`,
`num_ctx` and `keep_alive` go to Ollama only), `think`, `system_prompt`
(source, path, text, SHA-256),
`environment` (Lion version, best-effort git commit/dirty, and
`ollama_version` for Ollama only), `suite` (scenario-and-seed identity hash
plus the scenario names), `pending` (the in-flight attempt while a scenario
runs, else `null`), `entries`, `metrics` and `extras`.

Each attempt records `outcome`, `termination`, tool names, steps, tokens,
durations (including teardown), final text, transcript path, the MCP
`prompt_name` and SHA-256 of the rendered user text, the tool-inventory hash,
the recovery fields, and per-attempt `metrics`:

- `expected_tool`: the scenario's primary tool; `first_tool_hit`,
  `expected_tool_called`, `expected_tool_succeeded`.
- `arguments_valid_before_normalization`: every call sent arguments in the
  shape the API expects (nothing had to be parsed or cleaned; for OpenRouter a
  cleanly parseable JSON string is the expected shape).
- `calls_count`, `tool_errors`, `repeated_calls` (same `(name, arguments)`
  twice in one attempt), `empty_final_answer`, `done_reasons`.

`metrics` aggregates the same names with explicit `numerator`/`denominator`/
`value` triples, so every ratio states its basis; harness errors stay visible
as `attempts.error` and inside the denominators instead of disappearing.
Recovery definitions (target tools only): `first_attempt_success` — the first
target call succeeded and the task passed; `recovered_success` — the task
passed although a target call had failed before (denominator: attempts with a
failed target call). The matrix scenarios and the redaction-off variant are
report-only: their outcomes and ratio count, but they never fail the run.
Technical task completion (the checks) and answer quality stay separate.

A `done_reason` outside `stop` and `tool_calls` (OpenAI-style providers report
`tool_calls` on tool turns) counts as abnormal.

The `suite.sha256` covers every scenario definition (request, expected tools,
MCP prompt, check) and the fixture seeds; the composition is pinned by a
harness regression test. Two reports are comparable when this hash, the model
digest, the system prompt hash and the options match.

The ledger fixtures are deterministic: the A/B states, the ADR and the
recording use fixed timestamps, so two runs produce identical references and
bytes. The only live values are the boot id, PID and start ticks in a
recording's segment header, which the liveness checks need.

## Evaluation report

After the run is finalized, `extras.evaluation` and one
`agentic-report-<run-id>.md` next to `report.json` summarize what happened:
one bounded, tool-less chat call (240 s, `max_tokens` 2048, temperature 0,
same provider) over a condensed summary of the report — run metadata,
provider, model and options, suite hash, aggregate metrics, and per attempt
the outcome, failure, tools, steps, durations, tokens, metrics and the final
answer clipped to 800 characters. Raw transcripts and the API key stay out of
the input. The system prompt makes the
model an evaluation analyst; `report.language` selects the appended
instruction (`english`/`german`, default English).

`extras.evaluation.status` is one of:

| Status | Meaning |
| --- | --- |
| `ok` | The report was written; `report_path` points at it. |
| `disabled` | `report.enabled = false`; no chat call happened. |
| `skipped` | The run state is not `complete` (preflight error, interrupted); no chat call happened. |
| `error` | The call failed or the answer was empty; `error` carries the message, warned on stderr, never raised. |

`just test-agent` runs pytest with `-s`, so the path of a written report is
printed at the end of the run.

## Determinism limits

`temperature 0` with a fixed seed is approximately deterministic only for the
same model digest and the same provider build; a different digest, quantization
or provider version can change the output, and `openrouter/free`/`:free`
routing may dispatch to a different upstream model per request (its digest is
empty and cannot pin comparability). Partial CPU offload changes speed,
not the sampling. Treat every result as advisory: a failing hard assertion is a
signal to look at the transcript, and the matrix ratio is a trend line, not a
gate. Run it when changing the MCP server, its prompts, its redaction or the
tool inventory — not on every change.

## Comparing runs

To compare models, system prompts, MCP prompts or tool descriptions:

1. Change one variable at a time and run the suite at least twice per variant;
   repetitions are your error bar, not the mean.
2. Check comparability first: same `suite.sha256`, same fixture seeds, and the
   same sampling options; note the provider, the model digest, the provider
   version and the `system_prompt.sha256` of every report.
3. Compare per scenario, not only aggregate: a better mean can hide a lost
   scenario. Read `outcome`/`termination` first, then `first_tool_hit`,
   `expected_tool_succeeded`, `task_completed`, recovery and efficiency
   (`steps`, durations, tokens, `repeated_calls`, `tool_errors`).
4. Read the final answers and `done_reasons` before trusting a score; an
   `empty_final_answer` or an abnormal `done_reason` (anything but `stop`/
   `tool_calls`) marks a truncated or unusable result. The generated
   `agentic-report-<run-id>.md` is a convenient first triage, not proof: it is
   model-written from the same summary.
5. The first scenario runs cold: check whether its duration or token counts
   skew the run before drawing conclusions.
6. MCP prompt scenarios (`review-prompt`) change the user turn; system-prompt
   variants change every request. Keep the two experiments separate.
