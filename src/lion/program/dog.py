"""The watch dog: compile the ``confirm`` rules into a Zsh artifact.

The dog is a ZLE ``accept-line`` guard (ADR 0014): it confirms critical input
before execution, independent of an active recording. Python never runs in the
key path; the shell side only loads one generated artifact. This module owns
that artifact and the on/off state:

- The **state is intent**: ``[watch] dog`` in ``config.toml`` (ADR 0009).
- The **artifact is derived**: ``$XDG_DATA_HOME/lion/dog/rules.zsh`` carries the
  patterns and rule ids compiled from the classifier rule set, never a second
  rule source. Its presence is the effective switch for Zsh; ``enable`` writes
  the config first and the artifact second (worst case: guard inactive), and
  ``disable`` removes the artifact first and flips the config second.
- Matching is best-effort: every ``guard: "confirm"`` rule is searched as an
  ERE over the raw ZLE buffer. A leading ``^`` is widened to
  ``(^|[;&|[:space:]])(sudo[[:space:]]+)?`` so ``sudo reboot`` and ``; rm -rf``
  are caught; over-triggering is harmless (the second Enter executes),
  under-triggering is the documented limit.

``status`` is read-only; only ``enable``/``disable`` (and the implicit refresh
of ``enable``) write, atomically and with mode ``600``.
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lion.classify.rules import GUARD_CONFIRM, ClassifierRuleError, Rule, RuleSet, load_ruleset
from lion.classify.text import has_controls
from lion.program import shell
from lion.program.checks import CheckStatus, DoctorContext, Finding
from lion.program.config import Config, ConfigError, config_path, load_config, write_config
from lion.program.storage import get_data_dir
from lion.program.zshsyntax import validate_zsh

__all__ = [
    "DogError",
    "DogReport",
    "DogStatus",
    "disable",
    "dog_checks",
    "dog_status",
    "enable",
    "get_dog_dir",
    "read_rules_hash",
    "render_rules",
    "rules_path",
    "transform_pattern",
    "write_rules",
]

#: The prefix class that widens a leading ``^`` for the raw-buffer search.
SHELL_PREFIX = r"(^|[;&|[:space:]])(sudo[[:space:]]+)?"

#: The artifact header carrying the rule set hash it was compiled from.
HASH_PREFIX = "# lion-dog-ruleset-hash: "

_HASH_LINE = re.compile(r"# lion-dog-ruleset-hash: ([0-9a-f]{64})")

#: Compiles every loaded pattern once; a bad pattern makes zsh write to stderr.
_COMPILE_LOOP = """
local pat
for pat in "${LION_DOG_PATTERNS[@]}"; do
  [[ "" =~ $pat ]]
done
}
_lion_dog_compile_check
"""


class DogError(ValueError):
    """The guard artifact cannot be rendered, compiled, written or removed."""


@dataclass(frozen=True)
class DogReport:
    """Messages for stdout and warnings for stderr of one dog operation."""

    messages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DogStatus:
    """The read-only guard status: config intent plus the derived artifact."""

    enabled: bool
    config_present: bool
    rules_hash: str
    confirm_rules: int
    artifact_present: bool
    artifact_hash: str | None
    artifact_stale: bool
    integration_mode: str
    integration_inserted: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON shape of ``lion watch dog status``."""
        return {
            "enabled": self.enabled,
            "config_path": str(config_path()),
            "config_present": self.config_present,
            "rules_hash": self.rules_hash,
            "confirm_rules": self.confirm_rules,
            "artifact_path": str(rules_path()),
            "artifact_present": self.artifact_present,
            "artifact_hash": self.artifact_hash,
            "artifact_stale": self.artifact_stale,
            "integration_mode": self.integration_mode,
            "integration_inserted": self.integration_inserted,
            "warnings": list(self.warnings),
        }


def get_dog_dir() -> Path:
    """Return the guard data directory."""
    return get_data_dir() / "dog"


def rules_path() -> Path:
    """Return the derived guard artifact path (the file may not exist)."""
    return get_dog_dir() / "rules.zsh"


def transform_pattern(match: str) -> str:
    """Widen a leading ``^`` for the raw-buffer search; copy everything else.

    The ZLE widget searches the raw input with ``=~`` (unanchored), so a bare
    ``^`` would only ever fire at the very start of the buffer. The widened
    prefix keeps printed text out (a word character before ``rm`` fails) while
    catching ``sudo reboot`` and ``; rm -rf``. Rules without a leading ``^``
    (the block-scope pipelines, the shared-prefix ``shell.rm.recursive``) are
    used unchanged.
    """
    if match.startswith("^"):
        return SHELL_PREFIX + match[1:]
    return match


def _quote_zsh(text: str) -> str:
    r"""Quote one literal as a single-quoted Zsh word (``'`` becomes ``'\''``)."""
    return "'" + text.replace("'", "'\\''") + "'"


def _confirm_rules(ruleset: RuleSet) -> tuple[Rule, ...]:
    return tuple(rule for rule in ruleset.rules if rule.guard == GUARD_CONFIRM)


def render_rules(ruleset: RuleSet) -> str:
    """Render the deterministic Zsh artifact for the rule set's confirm rules.

    The output is a pure function of the merged rule list: header hash, the
    transformed patterns and the parallel rule ids, in rule-set order. Patterns
    with control characters are refused loudly (they can only come from a
    hand-crafted user file and would render into unreadable Zsh).

    Raises:
        DogError: If a confirm rule carries control characters in ``match``.
    """
    confirm = _confirm_rules(ruleset)
    lines = [f"{HASH_PREFIX}{ruleset.hash}", "typeset -ga LION_DOG_PATTERNS=("]
    for rule in confirm:
        if has_controls(rule.match):
            raise DogError(f"Rule '{rule.id}' has control characters in its pattern; refusing to compile it.")
        lines.append(f"  {_quote_zsh(transform_pattern(rule.match))}")
    lines.append(")")
    lines.append("typeset -ga LION_DOG_IDS=(")
    for rule in confirm:
        lines.append(f"  {_quote_zsh(rule.id)}")
    lines.append(")")
    return "\n".join(lines) + "\n"


def _zsh_compile_check(text: str) -> None:
    """Compile every rendered pattern once in one Zsh process.

    This is the ERE safety net between Python ``re`` and glibc ERE. Zsh reports
    a failed ``regcomp`` on stderr (status stays 1, the same as "no match"), so
    any stderr output counts as a failure; diagnostics are never echoed.

    Raises:
        DogError: If ``zsh`` is missing or a pattern fails to compile.
    """
    zsh = shutil.which("zsh")
    if zsh is None:
        raise DogError("zsh is required to compile the guard patterns; no artifact was written.")
    source = "function _lion_dog_compile_check() {\n" + text + _COMPILE_LOOP
    result = subprocess.run([zsh, "-f", "-c", source], text=True, capture_output=True, timeout=10, check=False)
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise DogError("A guard pattern failed the Zsh regex check; no artifact was written.")


def _write_atomic(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` atomically (mode ``600``), replacing it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            _ = stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def write_rules(ruleset: RuleSet | None = None) -> Path:
    """Render, validate and atomically write the guard artifact.

    The rendered text passes ``zsh -n`` and one Zsh process that compiles every
    pattern; any failure raises loudly and writes nothing. A repeated write is
    an idempotent refresh of the same path.

    Args:
        ruleset: Optional pre-loaded rule set; loaded from the standard layers
            when omitted.

    Returns:
        The path of the written artifact.

    Raises:
        DogError: If a pattern is refused or fails the Zsh check.
        ValueError: If ``zsh`` is unavailable for the syntax check.
        ClassifierRuleError: If the classifier rule set cannot be loaded.
        OSError: If the artifact cannot be written.
    """
    loaded = load_ruleset() if ruleset is None else ruleset
    text = render_rules(loaded)
    validate_zsh(text)
    _zsh_compile_check(text)
    return _write_atomic(rules_path(), text)


def read_rules_hash() -> str | None:
    """Return the rule-set hash from the artifact header, or ``None``.

    ``None`` means the artifact is missing, unreadable or does not carry the
    header (so it can never count as fresh).
    """
    try:
        text = rules_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    first_line = text.split("\n", 1)[0].strip()
    found = _HASH_LINE.fullmatch(first_line)
    return found.group(1) if found is not None else None


def _integration_info(home: Path | None) -> tuple[str, bool, str]:
    """Return ``(mode, inserted, error)`` of the watch shell integration."""
    try:
        target = shell.get_home() if home is None else home
        result = shell.status(target)
    except (OSError, ValueError) as exc:
        return "unknown", False, f"Cannot read the watch integration state: {exc}"
    return str(result.get("mode", "")), bool(result.get("inserted")), ""


def dog_status(home: Path | None = None) -> DogStatus:
    """Return the read-only guard status without writing.

    Loads the config and the merged rule set loudly (a broken file fails with
    its path), then compares the config intent with the derived artifact and
    the watch integration. It never creates a directory or a file.

    Args:
        home: Managed home for the integration state; resolved like
            ``lion shell status`` when omitted.

    Raises:
        ConfigError: If the config file is unreadable or invalid.
        ClassifierRuleError: If the classifier rule set cannot be loaded.
    """
    config_file = config_path()
    config_present = config_file.is_symlink() or config_file.exists()
    config = load_config()
    ruleset = load_ruleset()
    artifact = rules_path()
    artifact_present = artifact.is_symlink() or artifact.exists()
    artifact_hash = read_rules_hash()
    mode, inserted, integration_error = _integration_info(home)
    warnings: list[str] = []
    if integration_error:
        warnings.append(integration_error)
    if config.watch_dog:
        if not inserted and not integration_error:
            warnings.append("Guard is enabled but the watch integration is not inserted; run 'lion shell insert'.")
        if not artifact_present:
            warnings.append("Guard is enabled but the compiled artifact is missing; run 'lion watch dog on'.")
        elif artifact_hash != ruleset.hash:
            warnings.append("Guard artifact is stale or unreadable; run 'lion watch dog on'.")
    elif artifact_present:
        warnings.append("Guard is disabled in the config but the artifact still exists; run 'lion watch dog off'.")
    return DogStatus(
        enabled=config.watch_dog,
        config_present=config_present,
        rules_hash=ruleset.hash,
        confirm_rules=len(_confirm_rules(ruleset)),
        artifact_present=artifact_present,
        artifact_hash=artifact_hash,
        artifact_stale=artifact_present and artifact_hash != ruleset.hash,
        integration_mode=mode,
        integration_inserted=inserted,
        warnings=tuple(warnings),
    )


def _integration_warning(home: Path | None) -> str:
    """Return the stderr warning for a missing integration, or an empty string."""
    _mode, inserted, error = _integration_info(home)
    if error:
        return error
    if inserted:
        return ""
    return "The watch integration is not inserted; run 'lion shell insert', then open a new shell."


def enable(home: Path | None = None) -> DogReport:
    """Turn the guard on: write the config, then the compiled artifact.

    A repeated ``on`` is an idempotent refresh that recompiles the rules. The
    order is deliberate: a compile or write failure after the config write
    leaves the guard inactive (fail-safe), and ``lion doctor`` reports the
    drift; a repeated ``on`` heals it.

    Raises:
        ConfigError: If the config file is unreadable or invalid.
        ClassifierRuleError: If the classifier rule set cannot be loaded.
        DogError: If a pattern is refused or fails the Zsh check.
        OSError: If the config or the artifact cannot be written.
    """
    ruleset = load_ruleset()
    previous = load_config()
    config_file = write_config(Config(watch_dog=True))
    artifact = write_rules(ruleset)
    count = len(_confirm_rules(ruleset))
    messages = (
        f"Guard {'refreshed' if previous.watch_dog else 'enabled'}: {config_file}",
        f"Guard rules written: {artifact} ({count} confirm rules)",
        "The guard starts in new shells; run 'exec zsh' or open a new shell.",
    )
    warning = _integration_warning(home)
    return DogReport(messages=messages, warnings=(warning,) if warning else ())


def _remove_artifact(artifact: Path) -> None:
    """Remove the artifact, refusing anything that is not a file or symlink."""
    if artifact.is_symlink() or artifact.is_file():
        artifact.unlink()
        return
    raise DogError(f"Refusing to remove non-regular file: {artifact}")


def disable(home: Path | None = None) -> DogReport:
    """Turn the guard off: remove the artifact, then write the config off.

    ``off`` without a config flag and without an artifact reports "already
    off" and writes nothing. A repeated ``off`` after partial drift removes
    the remaining artifact.

    Raises:
        ConfigError: If the config file is unreadable or invalid.
        DogError: If a non-regular file blocks the artifact path.
        OSError: If the artifact or the config cannot be written.
    """
    previous = load_config()
    artifact = rules_path()
    artifact_present = artifact.is_symlink() or artifact.exists()
    if not previous.watch_dog and not artifact_present:
        return DogReport(messages=("Guard is already off; no files changed.",))
    messages: list[str] = []
    if artifact_present:
        _remove_artifact(artifact)
        messages.append(f"Guard rules removed: {artifact}")
    if previous.watch_dog:
        config_file = write_config(Config(watch_dog=False))
        messages.append(f"Guard disabled: {config_file}")
    warning = _integration_warning(home)
    return DogReport(messages=tuple(messages), warnings=(warning,) if warning else ())


def _state_finding(status: DogStatus) -> Finding:
    """Map the guard state to its one primary finding."""
    if not status.enabled:
        if status.artifact_present:
            return Finding(
                topic="watch",
                name="watch.dog",
                status=CheckStatus.WARN,
                message=f"Guard is disabled but the compiled artifact still exists: {rules_path()}",
                hint="Run 'lion watch dog off' to remove it.",
            )
        return Finding(
            topic="watch",
            name="watch.dog",
            status=CheckStatus.SKIP,
            message="Guard is off.",
            hint="Optional: 'lion watch dog on' confirms critical input in new shells.",
        )
    if not status.artifact_present:
        return Finding(
            topic="watch",
            name="watch.dog",
            status=CheckStatus.WARN,
            message=f"Guard is enabled but the compiled artifact is missing: {rules_path()}",
            hint="Run 'lion watch dog on' to compile the rules.",
        )
    if status.artifact_stale:
        return Finding(
            topic="watch",
            name="watch.dog",
            status=CheckStatus.WARN,
            message=f"Guard artifact is stale or unreadable: {rules_path()}",
            hint="Run 'lion watch dog on' to recompile the rules after a change.",
        )
    return Finding(
        topic="watch",
        name="watch.dog",
        status=CheckStatus.OK,
        message=f"Guard armed ({status.confirm_rules} confirm rules).",
    )


def dog_checks(ctx: DoctorContext) -> list[Finding]:
    """Check the guard state and artifact freshness (doctor topic ``watch``).

    Read-only: it loads the config and rule set, reads the artifact header and
    inspects the watch integration, and never writes.
    """
    try:
        status = dog_status(ctx.home)
    except ConfigError as exc:
        return [
            Finding(
                topic="watch",
                name="watch.dog",
                status=CheckStatus.ERROR,
                message=str(exc),
                hint="Fix or remove the config file; 'lion watch dog status' fails the same way.",
            )
        ]
    except ClassifierRuleError as exc:
        return [
            Finding(
                topic="watch",
                name="watch.dog",
                status=CheckStatus.ERROR,
                message=str(exc),
                hint="Fix or remove the user classifier file, or keep the built-in defaults only.",
            )
        ]
    findings = [_state_finding(status)]
    if status.enabled and not status.integration_inserted:
        detail = (
            status.warnings[0]
            if status.integration_mode == "unknown" and status.warnings
            else "Guard is enabled but the watch integration is not inserted."
        )
        findings.append(
            Finding(
                topic="watch",
                name="watch.dog.integration",
                status=CheckStatus.WARN,
                message=detail,
                hint="Run 'lion shell insert', then open a new shell.",
            )
        )
    return findings
