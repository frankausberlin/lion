"""Read-only doctor checks derived from a fresh state and the tool policy.

This module consumes the finished state mapping only and imports no collector
module, so it cannot create an import cycle with the collectors. The tool list
comes from :mod:`lion.state.tooling` and the canonical collector list from
:mod:`lion.state.registry`.
"""

from typing import cast

from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.state import tooling
from lion.state.registry import COLLECTORS

# Distribution names that belong to the Debian family (``apt``/``dpkg``).
_DEBIAN_FAMILIES = ("debian", "ubuntu", "linux mint", "pop!_os", "pop os", "raspbian", "kali")


def _section(state: dict[str, dict[str, object]], name: str) -> dict[str, object]:
    """Return one collector section, or an empty mapping when it is missing."""
    section = state.get(name)
    return section if isinstance(section, dict) else {}


def _text(section: dict[str, object], key: str) -> str:
    """Return a string field, or an empty string for a missing/non-string value."""
    value = section.get(key)
    return value if isinstance(value, str) else ""


def _is_debian(distribution: str) -> bool:
    """Return whether a distribution name belongs to the Debian family."""
    lowered = distribution.lower()
    return any(family in lowered for family in _DEBIAN_FAMILIES)


def collector_checks(ctx: DoctorContext) -> list[Finding]:
    """Check that every canonical collector produced a usable section.

    An incomplete collector is a ``warn`` with its section error, not an
    ``error``: a missing external tool or file is an environment fact, not a
    defect in LION itself.
    """
    findings: list[Finding] = []
    for collector in COLLECTORS:
        section = _section(ctx.state, collector.name)
        status = _text(section, "status")
        error = _text(section, "error")
        if not section:
            findings.append(
                Finding(
                    topic="collectors",
                    name=f"collectors.{collector.name}",
                    status=CheckStatus.WARN,
                    message=f"Collector '{collector.name}' fehlt in der Erfassung.",
                    hint="COLLECTORS und die Erfassung prüfen.",
                )
            )
        elif status == "ok":
            findings.append(
                Finding(
                    topic="collectors",
                    name=f"collectors.{collector.name}",
                    status=CheckStatus.OK,
                    message=f"Collector '{collector.name}': ok.",
                )
            )
        else:
            detail = error or "keine Angabe"
            findings.append(
                Finding(
                    topic="collectors",
                    name=f"collectors.{collector.name}",
                    status=CheckStatus.WARN,
                    message=f"Collector '{collector.name}': {status or 'unbekannt'} ({detail}).",
                    hint="Fehlende Quelle oder Rechte prüfen; die Erfassung bleibt unvollständig.",
                )
            )
    return findings


def _requirement(name: str, gpu_vendor: str, compute_platform: str, distribution: str) -> tuple[bool, str]:
    """Return whether a tool is required and a short human reason."""
    if name == "lspci":
        return True, "Quelle der GPU-Erkennung"
    if name == "nvidia_smi":
        if gpu_vendor == "nvidia" or compute_platform == "cuda":
            return True, f"GPU-Profil {gpu_vendor or compute_platform}"
        return False, "kein NVIDIA-/CUDA-Profil erkannt"
    if name == "apt_mark":
        if _is_debian(distribution):
            return True, f"Debian-Familie ({distribution})"
        return False, f"keine Debian-Familie ({distribution or 'unbekannt'})"
    if name == "zsh":
        return False, "nur für 'lion shlib install/uninstall' nötig"
    if name == "rocm_smi":
        return False, "von keinem Collector genutzt"
    return False, "unbekanntes Werkzeug"


def _remediation(name: str, distribution: str) -> tuple[tuple[str, ...], str]:
    """Return safe executable commands and a manual hint for a missing tool.

    Only package names that are certainly known are turned into executable
    lines. Everything uncertain (the NVIDIA driver package, ``apt-mark``) stays
    a comment-only hint. The commands are built from constants only; no tool
    output is ever interpolated.
    """
    if name == "lspci":
        if _is_debian(distribution):
            return ("sudo apt install pciutils",), "Installiere pciutils, das 'lspci' bereitstellt."
        return (), "Installiere das Paket, das 'lspci' bereitstellt (z. B. pciutils)."
    if name == "nvidia_smi":
        return (), "Installiere den passenden NVIDIA-Treiber; das Paket ist distributionsabhängig."
    if name == "apt_mark":
        return (), "Installiere APT/Dpkg, das 'apt-mark' bereitstellt."
    return (), ""


def tool_checks(ctx: DoctorContext) -> list[Finding]:
    """Check the external tools against the profile-dependent policy.

    A required tool that is missing is a ``warn`` with remediation; a tool that
    is not required for the detected profile is a neutral ``skip``.
    """
    hardware = _section(ctx.state, "hardware")
    host = _section(ctx.state, "host")
    tools = _section(ctx.state, "tools")
    raw_available = tools.get("available")
    available = cast("dict[str, object]", raw_available) if isinstance(raw_available, dict) else {}
    gpu_vendor = _text(hardware, "gpu_vendor")
    compute_platform = _text(hardware, "compute_platform")
    distribution = _text(host, "distribution")

    findings: list[Finding] = []
    for name, command in tooling.TOOLS:
        present = bool(available.get(name))
        required, reason = _requirement(name, gpu_vendor, compute_platform, distribution)
        if not required:
            presence = "vorhanden" if present else "nicht vorhanden"
            findings.append(
                Finding(
                    topic="tools",
                    name=f"tools.{name}",
                    status=CheckStatus.SKIP,
                    message=f"Nicht erforderlich ({reason}); {presence}.",
                )
            )
            continue
        if present:
            findings.append(
                Finding(
                    topic="tools",
                    name=f"tools.{name}",
                    status=CheckStatus.OK,
                    message=f"Verfügbar: {command}.",
                )
            )
            continue
        commands, hint = _remediation(name, distribution)
        findings.append(
            Finding(
                topic="tools",
                name=f"tools.{name}",
                status=CheckStatus.WARN,
                message=f"Erforderlich ({reason}), aber '{command}' wurde nicht gefunden.",
                hint=hint,
                commands=commands,
            )
        )
    return findings
