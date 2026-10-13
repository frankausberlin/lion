# 0018 — The MCP server is a read-only mirror plus ADR writing

- **Status:** accepted
- **Date:** 2026-10-13

## Context

Phase 5 promised an MCP server so agent harnesses can work with the ledger.
Everything it needs exists as a program layer and as `--json` CLI contracts:
history and snapshots (`program/storage.py`), recordings
(`program/recording.py`), ADR annotations (`program/annotations.py`),
standalone ADRs (`program/adrs.py`), the classifier (`classify/`) and the
derived wiki (`program/wiki.py`). [ADR 0017](0017-standalone-adrs.md) created
the standalone ADR store explicitly as the write target for this phase.

Two risks shape the decision. First, a second read path could drift from the
CLI; second, recorded text and ADR values can carry secrets, and the zsh-side
watch mask ([ADR 0011](0011-watch-capture-zsh-spool.md)) only covers what is
recorded, not what an agent reads.

## Decision

`lion serve` runs an **MCP server on stdio**, built with **fastmcp** (v4, the
modern protocol era with legacy negotiation) and imported lazily, so the
ordinary CLI never pays the import price. The command is a thin shell in
`command/serve.py`; the server definition lives in `program/serve.py`.

- **A view, not a second implementation.** Every tool calls the program layer
  and mirrors an existing `--json` contract; the test suite pins the payloads
  against the CLI output for the same ledger. There is no new data model and no
  new persisted state.
- **Read-only except two tools.** `lion_adr_add` and `lion_adr_update` are the
  only writers and touch only `$XDG_DATA_HOME/lion/adrs/` under the rules of
  ADR 0017. No tool collects a state, starts or stops a recording, edits a
  recording overlay, or executes a recommended script. The read-only proof is
  part of the tests (byte-identical trees after every read call).
- **Eleven tools, four resources, three prompts.** Tools: `lion_history`,
  `lion_snapshot`, `lion_diff`, `lion_recordings`, `lion_recording`,
  `lion_classify`, `lion_adr_list`, `lion_adr_show`, `lion_adr_add`,
  `lion_adr_update`, `lion_search`. Resources: `lion://wiki/index`,
  `lion://wiki/{collector}`, `lion://collectors`, `lion://classifier/rules`.
  Prompts: `review_latest_changes`, `document_decision`, `recall_knowledge`.
- **Loud errors.** Tool failures surface as MCP tool errors with the program
  layer's message, including paths and hints (`run 'lion scan'`); resources
  fail with the same clarity. Nothing is silently empty when the ledger is
  damaged or missing.
- **Redaction as a display filter.** A Python mirror of the zsh mask
  (`program/redaction.py`) masks `NAME=VALUE` assignments with keyword-bearing
  names and `Authorization:` headers in every text that comes from recordings
  or ADR fields — recording details, notes, classifier blocks and matches, ADR
  fields, titles, anchors, issues and search snippets. It is best-effort and
  not a security boundary; `LION_SERVE_REDACT=0` disables it. Snapshots, wiki
  pages and the classifier rule set stay untouched (collector facts, not shell
  input).
- **Prompts are package data.** The three defaults are Markdown files with a
  fixed TOML frontmatter and `{placeholder}` bodies under
  `program/prompt_defaults/`, read once at server start. A missing, extra,
  unreadable or invalid file fails the start loudly with its path. No hot
  reload, no user override folder in this version.
- **doctor.** A new read-only topic `serve` carries `serve.definition`: it
  builds the server (registration and prompt parsing) and reports an `error`
  with the underlying message when that fails. It never starts the server.
- **stdio only, no network.** stdout carries pure JSON-RPC, diagnostics go to
  stderr, the startup banner is disabled. There is no HTTP transport, no
  config file and no network access in this phase.

## Consequences

- Agents can read the ledger and document decisions without shell access;
  nothing they do can mutate the history, the recordings or the wiki.
- The CLI stays the single source of truth for the payload shapes: a contract
  test compares every tool against its `--json` counterpart, so drift fails
  the suite instead of shipping.
- The redaction filter is a deliberate copy of the zsh mask semantics (keyword
  gate, value forms, header form); the two are documented together and both
  are explicitly best-effort.
- `fastmcp` becomes the first runtime dependency of the project; it is
  imported only inside `build_server()`.
- `doctor` gains the eleventh topic; `scan`, `status`, `watch`, `records`,
  `adr` and `classify` are untouched. The `scan`/`status`/`watch` JSON
  contracts stay unchanged.

## Alternatives

- **Implement MCP by hand over the JSON-RPC wire.** Rejected: the protocol
  surface (initialization, capabilities, prompts, resource templates, error
  shapes) is large and versioned; fastmcp tracks both protocol eras.
- **Expose the CLI text output instead of the JSON contracts.** Rejected:
  text is a human view and would force agents to parse it; the `--json`
  contracts already exist and are pinned by tests.
- **Make the server able to scan.** Rejected: `scan` is the write path of the
  machine state and belongs to the human (ADR 0002); a remote trigger would
  blur the write boundary.
- **Allow editing recording-bound ADRs over MCP.** Rejected for this phase:
  the overlay writer belongs to `lion records edit`, which a human runs; the
  MCP write path stays with standalone ADRs.
- **User-overridable prompts via `$XDG_CONFIG_HOME/lion/prompts/`.** Deferred:
  the three defaults are enough to prove the mechanism; an override folder
  adds a merge and validation surface that should be decided separately.
- **HTTP transport or authentication.** Rejected for this phase: the server is
  a local stdio process under the user's account, consistent with the write
  boundary (ADR 0001).
