# Roadmap

This file records the intended order of the lion shlib/dash/security work. It
is a plan, not a promise; ADRs remain the maintainer's record of decisions.

1. **`lion doctor` and the redaction core** (implemented). Read-only diagnosis
   with a narrowly scoped `--fix`, a three-stage best-effort detector
   (prefixes, context rules, optional Gitleaks with `--deep`), confidence
   scoring, masking and HMAC fingerprints. See [doctor.md](doctor.md).
2. **MCP resources and `diagnose` tool.** Expose `shlibs`, `exports`,
   `dash/entries`, `dash/locked` and `dash/unlocked` as resources, and add a
   `diagnose` tool that reuses `collect_diagnosis`. Results carry metadata
   only: paths, line numbers, names, kinds, counts and modes; no contents,
   values, diffs or masked snippets.
3. **Masked dash content and diff tool.** Show lock/target content and diffs
   only through the redaction core, with no unmasked mode. The prefix list is
   prepared for the same masking, but locks and dash targets are not scanned
   yet.
4. **Optional write actions and `lion mcp --home`.** Add explicit, auditable
   write operations and a home override for the MCP server.
