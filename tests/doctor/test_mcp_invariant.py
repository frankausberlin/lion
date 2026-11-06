"""MCP metadata invariant.

MCP tools and resources may expose paths, line numbers, names, kinds, counts
and modes. They must never expose file contents, values, diffs or masked
snippets. ``Finding`` has no value field, and Gitleaks is no security boundary.
"""

from __future__ import annotations

from lion.doctor import collect_diagnosis
from lion.doctor.render import render_report
from lion.shlib.layout import ShlibLayout

TOKEN = "ghp_aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW"


def test_diagnosis_never_contains_the_secret_value(installed: ShlibLayout) -> None:
    """The reusable diagnosis core stays metadata-only."""
    path = installed.exports / "GITHUB_TOKEN"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)

    report = collect_diagnosis(installed)
    findings = [finding for finding in report.findings if finding.code == "exports.possible_secret"]

    assert findings
    assert TOKEN not in repr(report)
    assert TOKEN not in repr(report.findings)
    assert TOKEN not in repr(findings[0])
    assert TOKEN not in "\n".join(render_report(report, color=False))
