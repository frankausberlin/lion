"""The ``lion doctor`` command: diagnose read-only and write a review artifact.

``doctor`` performs a fresh, read-only collection and checks the collectors,
tools, history, storage and shlib. Problems (``warn``/``error``) produce exactly
one reviewable ``reco.sh`` under ``$XDG_DATA_HOME/lion/recos``; LION never runs
it. A pure ``ok`` run writes nothing at all.
"""

from pathlib import Path

import typer

from lion import __version__
from lion.program import doctor, shlib
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.storage import publish_reco
from lion.state.collector import collect_state
from lion.state.registry import COLLECTORS

_NO_HOME_HINT = "ZDOTDIR entfernen oder auf das Benutzer-Home richten; shlib verwaltet nur ~/.zshrc."


def _host_label(state: dict[str, dict[str, object]]) -> str:
    """Return a short ``hostname (distribution version)`` label."""
    section = state.get("host")
    host = section if isinstance(section, dict) else {}
    hostname = host.get("hostname", "?")
    detail = " ".join(
        part for part in (str(host.get("distribution", "")), str(host.get("distribution_version", ""))) if part
    )
    return f"{hostname} ({detail})" if detail else str(hostname)


def _resolve_home() -> tuple[Path, str]:
    """Return the managed home and a non-empty error message when it is foreign."""
    try:
        return shlib.get_home(), ""
    except ValueError as exc:
        return Path.home(), str(exc)


def _shlib_installed(home: Path) -> bool:
    """Return whether a managed shlib block is present under ``home``."""
    try:
        return bool(shlib.status(home).get("installed"))
    except (OSError, ValueError):
        return False


def _with_extra(findings: list[Finding], finding: Finding) -> list[Finding]:
    """Return the findings with one added, keeping the canonical order."""
    return doctor.sort_findings([*findings, finding])


def run(json_output: bool = False, show: bool = False) -> None:
    """Run the read-only diagnosis and publish a reco script when needed.

    Args:
        json_output: Emit a single JSON object instead of human text.
        show: In text mode, also print the generated reco script to stdout.
    """
    home, home_error = _resolve_home()
    state = collect_state(COLLECTORS)
    findings = doctor.run_checks(DoctorContext(state=state, home=home))
    if home_error:
        if _shlib_installed(home):
            findings = _with_extra(
                findings,
                Finding(
                    topic="shlib",
                    name="shlib.home",
                    status=CheckStatus.ERROR,
                    message=home_error,
                    hint=_NO_HOME_HINT,
                ),
            )
        else:
            findings = _with_extra(
                findings,
                Finding(
                    topic="shlib",
                    name="shlib.home",
                    status=CheckStatus.SKIP,
                    message=f"Kein verwaltetes Shlib; {home_error}",
                    hint=_NO_HOME_HINT,
                ),
            )

    status = doctor.overall(findings)
    reco_path: Path | None = None
    content = ""
    if status in (CheckStatus.WARN, CheckStatus.ERROR):
        content = doctor.render_reco(findings, __version__, _host_label(state))
        try:
            reco_path = publish_reco(content)
        except OSError as exc:
            findings = _with_extra(
                findings,
                Finding(
                    topic="storage",
                    name="storage.reco_publish",
                    status=CheckStatus.ERROR,
                    message=f"reco.sh konnte nicht geschrieben werden: {exc}",
                    hint="Schreibrechte auf $XDG_DATA_HOME/lion/recos prüfen.",
                ),
            )
            status = CheckStatus.ERROR
            content = doctor.render_reco(findings, __version__, _host_label(state))

    if json_output:
        typer.echo(doctor.render_json(findings, reco_path))
    else:
        typer.echo(doctor.render_text(findings, reco_path))
        if show and reco_path is not None:
            typer.echo("")
            typer.echo(content)

    if status == CheckStatus.ERROR:
        raise typer.Exit(code=1)
