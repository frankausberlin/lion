# lion

**LION — Linux Operator Nerd** collects basic Linux system information, saves
snapshots, and shows the latest saved state for people, scripts, and agents.
The project and CLI are named `lion`; the repository and Python distribution/import
package remain `repolion`.

## Getting started

Requires Linux and Python 3.12 or newer.

```bash
uv sync
uv run lion scan             # collect, display and save a scan
uv run lion status           # display the latest saved scan; does not rescan
uv run lion --help
```

No initialization step is needed. `scan` creates its data directory automatically.
The former `lion init` command has been removed.

Scans include hostname, distribution/version, kernel, architecture, CPU model,
logical CPU count, and total memory. Missing CPU/distribution details appear as
`Unknown`; missing or malformed memory readings and unavailable CPU counts use `0`.
The human-readable memory value is rounded down to whole GiB.

## JSON output

```bash
uv run lion scan --json
uv run lion status --json
```

Both commands output a single JSON value on stdout using the same structure as the
saved TOML record:

```json
{
  "scan": {"timestamp": "2026-10-05T20:00:00.123456+00:00"},
  "system": {
    "distribution": "Example Linux",
    "distribution_version": "1.0",
    "kernel": "6.0.0",
    "architecture": "x86_64",
    "hostname": "workstation",
    "cpu_model": "Example CPU",
    "cpu_logical_cores": 8,
    "memory_total_bytes": 17179869184
  }
}
```

`scan --json` still saves the scan; its timestamp and fields match that saved record.
With no saved scans, `status --json` outputs `null` and exits successfully.
Operational or invalid-scan errors go to stderr with exit code `1` and no JSON on
stdout. Memory is an integer byte count, and timestamps include a UTC offset.

## Storage and compatibility

Scans are TOML files under `$XDG_DATA_HOME/lion/scans`, defaulting to
`~/.local/share/lion/scans`.

New records use UTC timestamps. Filenames include microseconds and a random suffix.
A completed temporary file is atomically published using a hard link, without
replacing any existing scan. This requires a filesystem supporting hard links;
publication failures are reported rather than falling back to an unsafe write.

Existing scans with local timezone offsets remain readable. `status` compares the
stored timestamps, so a daylight-saving clock change does not reverse their order.
If timestamps are identical, filenames provide a deterministic tie-breaker.
All `.toml` scans are validated: any unreadable or invalid scan stops `status` with
its path in the error message. LION does not silently skip damaged files and report
an older scan as current. Temporary files are ignored.

## Development

```bash
just test       # tests and coverage; minimum 90%
just lint       # lint, formatting and type check
just fix        # auto-fix lint issues
just check      # full quality gate
```

Tests cover fixture-based Linux readings, legacy timestamps, collisions, publication
failures, invalid stored data, and text/JSON CLI behavior. CLI smoke tests also
exercise the local Linux system.

## Release

```bash
just bump patch
git push origin main --tags
```

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
