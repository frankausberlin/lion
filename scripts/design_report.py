#!/usr/bin/env python3
"""Lean design report for the Luxus Python Stack.

The report measures the current source tree with radon and vulture. It is
advisory: findings never fail the quality gate by themselves.

Commands
--------
run
    Write ``.refactor/report.json`` and ``.refactor/report.md``.
save NAME
    Run the measurement and snapshot it under ``.refactor/baselines/NAME``.
    An existing baseline is never overwritten.
compare NAME
    Run the measurement and compare it with the baseline ``NAME``. The
    comparison succeeds when it completes; metric regressions are reported.
    A regression is a finding, not an operational error.

Exit codes: 0 success, 1 operational error, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

SCHEMA = "lupysta-design-report-v1"
CONTRACT_VERSION = "1"
ROOT = Path(__file__).resolve().parents[1]
REFACTOR_DIR = ROOT / ".refactor"
REPORT_JSON = REFACTOR_DIR / "report.json"
REPORT_MD = REFACTOR_DIR / "report.md"
INCOMPLETE_MARKER = REFACTOR_DIR / "incomplete"
BASELINE_DIR = REFACTOR_DIR / "baselines"

EXIT_OK = 0
EXIT_OPERATIONAL = 1
EXIT_USAGE = 2


class MeasurementError(RuntimeError):
    """Raised when a measurement tool fails or returns invalid data."""


class UsageError(RuntimeError):
    """Raised for invalid command-line values."""


def _as_dict(value: object, label: str) -> dict[str, object]:
    """Return *value* as a JSON object or raise :class:`MeasurementError`."""
    if not isinstance(value, dict):
        raise MeasurementError(f"{label}: expected a JSON object")
    return cast(dict[str, object], value)


def _as_list(value: object, label: str) -> list[object]:
    """Return *value* as a JSON array or raise :class:`MeasurementError`."""
    if not isinstance(value, list):
        raise MeasurementError(f"{label}: expected a JSON array")
    return cast(list[object], value)


def _number(value: object, default: float = 0.0) -> float:
    """Return a finite number from *value*, or *default*."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _text(value: object, default: str = "") -> str:
    """Return a string from *value*, or *default*."""
    return value if isinstance(value, str) else default


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run *command* in the project root and capture its output."""
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MeasurementError(f"command not found: {command[0]}") from exc


def _tool_command(name: str) -> list[str]:
    """Return a command for *name*, preferring the executable on PATH."""
    executable = shutil.which(name)
    if executable is not None:
        return [executable]
    return [sys.executable, "-m", name]


def _tool_version(name: str) -> str:
    """Return the first version line of *name*, or ``unavailable``."""
    try:
        result = _run([*_tool_command(name), "--version"])
    except MeasurementError:
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    first_line = result.stdout.strip().splitlines()
    return first_line[0] if first_line else "unknown"


def _json_tool(name: str, arguments: list[str]) -> object:
    """Run *name* with *arguments* and parse its JSON output."""
    result = _run([*_tool_command(name), *arguments])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise MeasurementError(f"{name} {' '.join(arguments)} failed: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MeasurementError(f"{name} returned invalid JSON: {exc}") from exc


def _sources() -> list[str]:
    """Return the existing source directories used for measurement."""
    directories = [name for name in ("src", "tests") if (ROOT / name).is_dir()]
    if not directories:
        raise MeasurementError("neither 'src' nor 'tests' exists")
    return directories


def _complexity_metrics(data: object) -> dict[str, object]:
    """Aggregate radon cyclomatic-complexity output."""
    blocks: list[dict[str, object]] = []
    for filename, value in _as_dict(data, "radon cc").items():
        for item in _as_list(value, f"radon cc[{filename}]"):
            entry = _as_dict(item, f"radon cc[{filename}] entry")
            complexity = _number(entry.get("complexity"))
            blocks.append(
                {
                    "file": filename,
                    "name": _text(entry.get("name")),
                    "type": _text(entry.get("type")),
                    "complexity": complexity,
                    "lineno": _number(entry.get("lineno")),
                }
            )
    scores = [_number(block["complexity"]) for block in blocks]
    top = sorted(blocks, key=lambda block: _number(block["complexity"]), reverse=True)[:10]
    return {
        "blocks": len(blocks),
        "average": round(statistics.mean(scores), 2) if scores else 0.0,
        "maximum": max(scores) if scores else 0.0,
        "high": sum(1 for score in scores if score >= 10.0),
        "top": top,
    }


def _maintainability_metrics(data: object) -> dict[str, object]:
    """Aggregate radon maintainability-index output."""
    files: list[dict[str, object]] = []
    for filename, value in _as_dict(data, "radon mi").items():
        entry = _as_dict(value, f"radon mi[{filename}]")
        files.append(
            {
                "file": filename,
                "mi": round(_number(entry.get("mi")), 2),
                "rank": _text(entry.get("rank")),
            }
        )
    scores = [_number(file["mi"]) for file in files]
    worst = sorted(files, key=lambda file: _number(file["mi"]))[:10]
    return {
        "files": len(files),
        "average": round(statistics.mean(scores), 2) if scores else 0.0,
        "minimum": min(scores) if scores else 0.0,
        "worst": worst,
    }


def _raw_metrics(data: object) -> dict[str, float]:
    """Aggregate radon raw-metrics output."""
    totals = {
        "loc": 0.0,
        "lloc": 0.0,
        "sloc": 0.0,
        "comments": 0.0,
        "blank": 0.0,
        "files": 0.0,
    }
    for filename, value in _as_dict(data, "radon raw").items():
        entry = _as_dict(value, f"radon raw[{filename}]")
        for key in ("loc", "lloc", "sloc", "comments", "blank"):
            totals[key] += _number(entry.get(key))
        totals["files"] += 1.0
    return totals


def _vulture_findings() -> dict[str, object]:
    """Return advisory vulture findings as text lines.

    Tests are scanned as well so that production code used only by tests is not
    reported as dead. Pytest fixtures and hooks are ignored because pytest
    invokes them implicitly instead of referencing them by name.
    """
    directories = _sources()
    result = _run(
        [
            *_tool_command("vulture"),
            *directories,
            "--min-confidence",
            "60",
            "--ignore-decorators",
            "@pytest*.fixture*",
            "--ignore-names",
            "pytest_*,pytestmark,collect_ignore,collect_ignore_glob",
        ]
    )
    if result.returncode not in (0, 3):
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise MeasurementError(f"vulture failed: {detail}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {"findings": len(lines), "items": lines[:20]}


def _revision() -> str:
    """Return the current Git revision or ``n/a``."""
    try:
        result = _run(["git", "rev-parse", "--short", "HEAD"])
    except MeasurementError:
        return "n/a"
    return result.stdout.strip() if result.returncode == 0 else "n/a"


def build_report() -> dict[str, object]:
    """Measure the project and return the report as a dictionary."""
    reasons: list[str] = []
    metrics: dict[str, object] = {}

    try:
        metrics["complexity"] = _complexity_metrics(_json_tool("radon", ["cc", "src", "--json"]))
    except MeasurementError as exc:
        reasons.append(str(exc))

    try:
        metrics["maintainability"] = _maintainability_metrics(_json_tool("radon", ["mi", "src", "--json"]))
    except MeasurementError as exc:
        reasons.append(str(exc))

    try:
        metrics["raw"] = _raw_metrics(_json_tool("radon", ["raw", "src", "--json"]))
    except MeasurementError as exc:
        reasons.append(str(exc))

    try:
        metrics["vulture"] = _vulture_findings()
    except MeasurementError as exc:
        reasons.append(str(exc))

    return {
        "schema": SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "complete": not reasons,
        "incomplete_reasons": reasons,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "revision": _revision(),
        "python": sys.version.split()[0],
        "tool_versions": {
            "radon": _tool_version("radon"),
            "vulture": _tool_version("vulture"),
        },
        "metrics": metrics,
    }


def _markdown_report(report: dict[str, object]) -> str:
    """Render *report* as Markdown."""
    metrics = _as_dict(report.get("metrics"), "metrics")
    complexity = _as_dict(metrics.get("complexity", {}), "complexity")
    maintainability = _as_dict(metrics.get("maintainability", {}), "maintainability")
    raw = _as_dict(metrics.get("raw", {}), "raw")
    vulture = _as_dict(metrics.get("vulture", {}), "vulture")
    complete = report.get("complete") is True

    lines = [
        "# Design report",
        "",
        f"- schema: `{_text(report.get('schema'))}`",
        f"- contract version: `{_text(report.get('contract_version'))}`",
        f"- revision: `{_text(report.get('revision'), 'n/a')}`",
        f"- generated: `{_text(report.get('generated_at'))}`",
        f"- python: `{_text(report.get('python'))}`",
        f"- complete: `{'yes' if complete else 'no'}`",
    ]
    reasons = _as_list(report.get("incomplete_reasons", []), "incomplete_reasons")
    if reasons:
        lines.extend(["", "## Incomplete", ""])
        lines.extend(f"- {_text(reason)}" for reason in reasons)

    lines.extend(
        [
            "",
            "## Complexity (radon cc)",
            "",
            f"- blocks: {_number(complexity.get('blocks')):.0f}",
            f"- average: {_number(complexity.get('average')):.2f}",
            f"- maximum: {_number(complexity.get('maximum')):.2f}",
            f"- high (>= 10): {_number(complexity.get('high')):.0f}",
            "",
            "| File | Block | Type | Complexity | Line |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for block in _as_list(complexity.get("top", []), "complexity.top"):
        entry = _as_dict(block, "complexity.top entry")
        lines.append(
            f"| `{_text(entry.get('file'))}` | `{_text(entry.get('name'))}` | "
            f"{_text(entry.get('type'))} | {_number(entry.get('complexity')):.0f} | "
            f"{_number(entry.get('lineno')):.0f} |"
        )

    lines.extend(
        [
            "",
            "## Maintainability (radon mi)",
            "",
            f"- files: {_number(maintainability.get('files')):.0f}",
            f"- average: {_number(maintainability.get('average')):.2f}",
            f"- minimum: {_number(maintainability.get('minimum')):.2f}",
            "",
            "| File | MI | Rank |",
            "| --- | ---: | --- |",
        ]
    )
    for file_entry in _as_list(maintainability.get("worst", []), "maintainability.worst"):
        entry = _as_dict(file_entry, "maintainability.worst entry")
        lines.append(f"| `{_text(entry.get('file'))}` | {_number(entry.get('mi')):.2f} | {_text(entry.get('rank'))} |")

    lines.extend(
        [
            "",
            "## Raw metrics (radon raw)",
            "",
            f"- files: {_number(raw.get('files')):.0f}",
            f"- LOC: {_number(raw.get('loc')):.0f}",
            f"- LLOC: {_number(raw.get('lloc')):.0f}",
            f"- SLOC: {_number(raw.get('sloc')):.0f}",
            f"- comments: {_number(raw.get('comments')):.0f}",
            f"- blank: {_number(raw.get('blank')):.0f}",
            "",
            "## Vulture (advisory)",
            "",
            f"- findings: {_number(vulture.get('findings')):.0f}",
            "",
        ]
    )
    for item in _as_list(vulture.get("items", []), "vulture.items"):
        lines.append(f"- `{_text(item)}`")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, object]) -> None:
    """Write the current report and update the incomplete marker."""
    REFACTOR_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    REPORT_MD.write_text(_markdown_report(report), encoding="utf-8")
    if report.get("complete") is True:
        INCOMPLETE_MARKER.unlink(missing_ok=True)
    else:
        reasons = _as_list(report.get("incomplete_reasons", []), "incomplete_reasons")
        INCOMPLETE_MARKER.write_text("\n".join(_text(reason) for reason in reasons) + "\n", encoding="utf-8")


def _validate_name(name: str) -> str:
    """Validate a baseline name and return it."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise UsageError("baseline name must be a simple name without path separators")
    return name


def _command_run() -> int:
    """Run the measurement and write the current report."""
    report = build_report()
    write_report(report)
    if report.get("complete") is True:
        print(f"Report: {REPORT_MD.relative_to(ROOT)}")
        return EXIT_OK
    print(f"Incomplete report: {REPORT_MD.relative_to(ROOT)}", file=sys.stderr)
    return EXIT_OPERATIONAL


def _command_save(name: str) -> int:
    """Save a baseline without overwriting an existing one."""
    baseline_name = _validate_name(name)
    baseline = BASELINE_DIR / baseline_name
    if baseline.exists():
        raise MeasurementError(f"baseline '{baseline_name}' already exists")
    report = build_report()
    write_report(report)
    if report.get("complete") is not True:
        raise MeasurementError("refusing to save an incomplete measurement")
    baseline.mkdir(parents=True)
    (baseline / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (baseline / "report.md").write_text(_markdown_report(report), encoding="utf-8")
    print(f"Baseline: {baseline.relative_to(ROOT)}")
    return EXIT_OK


def _metric(report: dict[str, object], *path: str) -> float:
    """Return a nested metric value, or 0.0 when it is missing."""
    value: object = report
    for key in path:
        try:
            value = _as_dict(value, "metric").get(key)
        except MeasurementError:
            return 0.0
    return _number(value)


def _compare_text(baseline: dict[str, object], current: dict[str, object]) -> str:
    """Return a Markdown comparison."""
    rules: tuple[tuple[str, tuple[str, ...], str, float], ...] = (
        ("Complexity average", ("metrics", "complexity", "average"), "higher", 0.5),
        ("Complexity maximum", ("metrics", "complexity", "maximum"), "higher", 1.0),
        ("Complexity high blocks", ("metrics", "complexity", "high"), "higher", 0.0),
        (
            "Maintainability average",
            ("metrics", "maintainability", "average"),
            "lower",
            1.0,
        ),
        (
            "Maintainability minimum",
            ("metrics", "maintainability", "minimum"),
            "lower",
            1.0,
        ),
        ("Vulture findings", ("metrics", "vulture", "findings"), "higher", 2.0),
    )
    lines = [
        "# Design comparison",
        "",
        f"- baseline revision: `{_text(baseline.get('revision'), 'n/a')}`",
        f"- current revision: `{_text(current.get('revision'), 'n/a')}`",
        "",
        "| Metric | Baseline | Current | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    regression_notes: list[str] = []
    for label, path, direction, threshold in rules:
        before = _metric(baseline, *path)
        after = _metric(current, *path)
        delta = after - before
        lines.append(f"| {label} | {before:.2f} | {after:.2f} | {delta:+.2f} |")
        worse = delta > threshold if direction == "higher" else delta < -threshold
        if worse:
            regression_notes.append(f"{label}: {before:.2f} -> {after:.2f}")

    lines.extend(
        [
            "",
            "## Regressions",
            "",
            "No regression detected." if not regression_notes else "\n".join(f"- {note}" for note in regression_notes),
            "",
            "Metrics are advisory. Confirm each change with tests and review.",
            "",
        ]
    )
    return "\n".join(lines)


def _command_compare(name: str) -> int:
    """Compare the current measurement with a saved baseline."""
    baseline_name = _validate_name(name)
    baseline_path = BASELINE_DIR / baseline_name / "report.json"
    if not baseline_path.is_file():
        raise MeasurementError(f"baseline '{baseline_name}' does not exist")
    try:
        baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeasurementError(f"cannot read baseline '{baseline_name}': {exc}") from exc
    baseline = _as_dict(baseline_data, "baseline")
    if baseline.get("complete") is not True:
        raise MeasurementError(f"baseline '{baseline_name}' is incomplete")
    current = build_report()
    write_report(current)
    if current.get("complete") is not True:
        raise MeasurementError("current measurement is incomplete")
    comparison = _compare_text(baseline, current)
    comparison_path = REFACTOR_DIR / f"compare-{baseline_name}.md"
    comparison_path.write_text(comparison, encoding="utf-8")
    print(comparison)
    print(f"Comparison: {comparison_path.relative_to(ROOT)}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(
        description="Measure the project and manage design baselines.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="write the current report")

    save_parser = subparsers.add_parser("save", help="save a baseline")
    save_parser.add_argument("name", help="baseline name")

    compare_parser = subparsers.add_parser("compare", help="compare the current report with a baseline")
    compare_parser.add_argument("name", help="baseline name")

    if argv is None:
        argv = sys.argv[1:] or ["run"]
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "run":
            return _command_run()
        if arguments.command == "save":
            return _command_save(arguments.name)
        if arguments.command == "compare":
            return _command_compare(arguments.name)
    except UsageError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except MeasurementError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
