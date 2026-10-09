"""The read-only ``doctor`` check framework and its aggregation.

``doctor`` aggregates checks owned by the modules that own the data: the
collector/tool checks live in :mod:`lion.state.diagnosis`, the history/storage
checks in :mod:`lion.program.storage` and the shlib check in
:mod:`lion.program.shlib`. This module only runs them, catches a crashing check,
orders the findings deterministically and renders text, JSON and the
human-executed ``reco.sh``.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from lion.program import shlib, storage
from lion.program.checks import Check, CheckStatus, DoctorContext, Finding
from lion.state import diagnosis

__all__ = [
    "CHECKS",
    "TOPIC_ORDER",
    "Check",
    "CheckStatus",
    "DoctorContext",
    "Finding",
    "overall",
    "render_json",
    "render_reco",
    "render_text",
    "run_checks",
    "sort_findings",
    "summary",
]

#: Semantic topic order used for output; kept aligned with :data:`CHECKS`.
TOPIC_ORDER: tuple[str, ...] = ("collectors", "tools", "history", "storage", "shlib")

#: The ordered checks ``doctor`` runs, one per entry of :data:`TOPIC_ORDER`.
CHECKS: tuple[Check, ...] = (
    diagnosis.collector_checks,
    diagnosis.tool_checks,
    storage.history_checks,
    storage.storage_checks,
    shlib.shlib_checks,
)

_TOPIC_INDEX = {topic: index for index, topic in enumerate(TOPIC_ORDER)}


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Return the findings ordered deterministically for every output."""
    return sorted(findings, key=lambda finding: (_TOPIC_INDEX.get(finding.topic, len(TOPIC_ORDER)), finding.name))


def run_checks(ctx: DoctorContext) -> list[Finding]:
    """Run every check and return the sorted findings.

    A check that raises unexpectedly becomes an ``error`` finding; no check can
    abort the run.
    """
    findings: list[Finding] = []
    for topic, check in zip(TOPIC_ORDER, CHECKS, strict=True):
        try:
            findings.extend(check(ctx))
        except Exception as exc:
            findings.append(
                Finding(
                    topic=topic,
                    name=f"{topic}.check",
                    status=CheckStatus.ERROR,
                    message=f"Check '{topic}' ist fehlgeschlagen: {exc}",
                    hint="Das ist ein LION-Fehler; bitte melden.",
                )
            )
    return sort_findings(findings)


def overall(findings: list[Finding]) -> CheckStatus:
    """Return the aggregate status: ``error`` > ``warn`` > ``ok``; ``skip`` neutral."""
    statuses = {finding.status for finding in findings}
    if CheckStatus.ERROR in statuses:
        return CheckStatus.ERROR
    if CheckStatus.WARN in statuses:
        return CheckStatus.WARN
    return CheckStatus.OK


def summary(findings: list[Finding]) -> dict[str, int]:
    """Return the count per status as German-keyed JSON-ready values."""
    counts = {"ok": 0, "warn": 0, "error": 0, "skip": 0}
    for finding in findings:
        counts[finding.status.value] += 1
    return counts


def _ordered_topics(findings: list[Finding]) -> list[str]:
    """Return the topics present, in the canonical order."""
    present = {finding.topic for finding in findings}
    ordered = [topic for topic in TOPIC_ORDER if topic in present]
    ordered.extend(sorted(present - set(TOPIC_ORDER)))
    return ordered


_STATUS_LABEL = {
    CheckStatus.OK: "ok",
    CheckStatus.WARN: "Warnung",
    CheckStatus.ERROR: "Fehler",
    CheckStatus.SKIP: "\u2013 (nicht zutreffend)",
}


def render_text(findings: list[Finding], reco_path: Path | None = None) -> str:
    """Render the findings as grouped German text for the terminal.

    Args:
        findings: Sorted findings.
        reco_path: The published recommendation script, or ``None``.

    Returns:
        The complete text output.
    """
    topics = _ordered_topics(findings)
    lines = ["lion doctor", "Geprüft: " + ", ".join(topics), ""]
    for topic in topics:
        lines.append(f"{topic}:")
        for finding in findings:
            if finding.topic != topic:
                continue
            lines.append(f"  [{_STATUS_LABEL[finding.status]}] {finding.name}: {finding.message}")
            if finding.hint:
                lines.append(f"      Abhilfe: {finding.hint}")
        lines.append("")
    counts = summary(findings)
    lines.append(f"Ergebnis: {counts['ok']} ok, {counts['warn']} warn, {counts['error']} error, {counts['skip']} skip")
    if reco_path is not None:
        lines.append(f"Behebungsskript: {reco_path}")
        lines.append("Erst vollständig lesen, dann ausführen. LION führt es niemals aus.")
    return "\n".join(lines)


def render_json(findings: list[Finding], reco_path: Path | None = None) -> str:
    """Render the findings as a single JSON object with German keys."""
    payload = {
        "status": overall(findings).value,
        "geprueft": _ordered_topics(findings),
        "befunde": [
            {
                "topic": finding.topic,
                "name": finding.name,
                "status": finding.status.value,
                "message": finding.message,
                "hint": finding.hint,
                "commands": list(finding.commands),
            }
            for finding in findings
        ],
        "zusammenfassung": summary(findings),
        "reco_pfad": None if reco_path is None else str(reco_path),
    }
    return json.dumps(payload)


def _one_line(text: str) -> str:
    """Collapse a value to one line so no embedded newline can inject a command."""
    return " ".join(text.splitlines())


def render_reco(findings: list[Finding], version: str, host: str) -> str:
    """Render the human-executed recommendation script.

    Every problem (``warn``/``error``) becomes a comment block; only findings
    that carry non-empty ``commands`` add executable lines. All comment text is
    prefixed line by line, so no message or host value can inject a command.

    Args:
        findings: Sorted findings.
        version: The LION version for the header.
        host: A short host label for the header.

    Returns:
        The complete script text.
    """
    lines = [
        "#!/usr/bin/env bash",
        "#",
        "# lion doctor — Behebungsvorschläge (reco)",
        f"# Erzeugt: {datetime.now(UTC).isoformat()}",
        f"# LION: {version}",
        f"# Host: {_one_line(host)}",
        "#",
        "# VOR DEM AUSFÜHREN KOMPLETT LESEN.",
        "# LION führt dieses Skript niemals aus und ändert selbst nichts.",
        "#",
        "set -euo pipefail",
        "",
    ]
    for finding in findings:
        if finding.status not in (CheckStatus.WARN, CheckStatus.ERROR):
            continue
        lines.append("# " + "=" * 60)
        lines.append(f"# [{_one_line(finding.topic)}] {_one_line(finding.name)}: {finding.status.value}")
        for line in finding.message.splitlines() or [""]:
            lines.append(f"#   {line}")
        if finding.hint:
            for line in finding.hint.splitlines():
                lines.append(f"# Abhilfe (manuell): {line}")
        lines.append("# " + "=" * 60)
        if finding.commands:
            lines.extend(finding.commands)
        else:
            lines.append("# Kein sicherer automatischer Befehl; siehe Hinweis oben.")
        lines.append("")
    return "\n".join(lines) + "\n"
