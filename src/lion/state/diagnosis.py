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
                    message=f"Collector '{collector.name}' is missing from the capture.",
                    hint="Check COLLECTORS and the capture.",
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
            detail = error or "no detail"
            findings.append(
                Finding(
                    topic="collectors",
                    name=f"collectors.{collector.name}",
                    status=CheckStatus.WARN,
                    message=f"Collector '{collector.name}': {status or 'unknown'} ({detail}).",
                    hint="Check for a missing source or permissions; the capture stays incomplete.",
                )
            )
    return findings


def _has_nvidia_gpu(hardware: dict[str, object]) -> bool:
    """Return whether any detected GPU is NVIDIA.

    The per-GPU list is authoritative, so a mixed AMD+NVIDIA machine still
    requires ``nvidia-smi``. A state without the list falls back to the
    aggregate vendor/compute hint.
    """
    raw = hardware.get("gpu")
    if isinstance(raw, list):
        for item in cast("list[object]", raw):
            if not isinstance(item, dict):
                continue
            entry = cast("dict[str, object]", item)
            if entry.get("vendor") == "nvidia" or entry.get("driver") == "nvidia":
                return True
        return False
    return _text(hardware, "gpu_vendor") == "nvidia" or _text(hardware, "compute_platform") == "cuda"


def _requirement(name: str, has_nvidia: bool, distribution: str) -> tuple[bool, str]:
    """Return whether a tool is required and a short human reason."""
    if name == "lspci":
        return True, "source of GPU detection"
    if name == "nvidia_smi":
        if has_nvidia:
            return True, "NVIDIA GPU detected"
        return False, "no NVIDIA GPU detected"
    if name == "apt_mark":
        if _is_debian(distribution):
            return True, f"Debian family ({distribution})"
        return False, f"not a Debian family ({distribution or 'unknown'})"
    if name == "systemctl":
        return True, "source of service unit states"
    if name in ("docker", "podman"):
        return False, "used by the containers collector, absence is reported there"
    if name == "zsh":
        return False, "only needed for 'lion shlib install/uninstall'"
    if name == "rocm_smi":
        return False, "used by no collector"
    return False, "unknown tool"


def _remediation(name: str, distribution: str) -> tuple[tuple[str, ...], str]:
    """Return safe executable commands and a manual hint for a missing tool.

    Only package names that are certainly known are turned into executable
    lines. Everything uncertain (the NVIDIA driver package, ``apt-mark``) stays
    a comment-only hint. The commands are built from constants only; no tool
    output is ever interpolated.
    """
    if name == "lspci":
        if _is_debian(distribution):
            return ("sudo apt install pciutils",), "Install pciutils, which provides 'lspci'."
        return (), "Install the package that provides 'lspci' (e.g. pciutils)."
    if name == "nvidia_smi":
        return (), "Install the matching NVIDIA driver; the package is distribution-specific."
    if name == "apt_mark":
        return (), "Install APT/Dpkg, which provides 'apt-mark'."
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
    has_nvidia = _has_nvidia_gpu(hardware)
    distribution = _text(host, "distribution")

    findings: list[Finding] = []
    for name, command in tooling.TOOLS:
        present = bool(available.get(name))
        required, reason = _requirement(name, has_nvidia, distribution)
        if not required:
            presence = "present" if present else "not present"
            findings.append(
                Finding(
                    topic="tools",
                    name=f"tools.{name}",
                    status=CheckStatus.SKIP,
                    message=f"Not required ({reason}); {presence}.",
                )
            )
            continue
        if present:
            findings.append(
                Finding(
                    topic="tools",
                    name=f"tools.{name}",
                    status=CheckStatus.OK,
                    message=f"Available: {command}.",
                )
            )
            continue
        commands, hint = _remediation(name, distribution)
        findings.append(
            Finding(
                topic="tools",
                name=f"tools.{name}",
                status=CheckStatus.WARN,
                message=f"Required ({reason}) but '{command}' was not found.",
                hint=hint,
                commands=commands,
            )
        )
    return findings
