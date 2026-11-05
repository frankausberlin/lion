# Roadmap

This file records the intended order of the lion shlib/dash/security work. It
is a plan, not a promise; ADRs remain the maintainer's record of decisions.

1. **`lion doctor` and the redaction core** (implemented). Read-only diagnosis
   with a narrowly scoped `--fix`, plus a dependency-free detector, confidence
   scoring, masking and HMAC fingerprints. See [doctor.md](doctor.md).
2. **MCP resources and `diagnose` tool.** Expose `shlibs`, `exports`,
   `dash/entries`, `dash/locked` and `dash/unlocked` as resources, and add a
   `diagnose` tool that reuses `collect_diagnosis`. No MCP change is part of
   step 1.
3. **Masked dash content and diff tool.** Show lock/target content and diffs
   only through the redaction core, with no unmasked mode.
4. **Optional write actions and `lion mcp --home`.** Add explicit, auditable
   write operations and a home override for the MCP server.
