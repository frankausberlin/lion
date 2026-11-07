"""User-maintainable secret prefix rules for best-effort token detection.

The built-in rules cover common provider token prefixes. A user file can add
rules, replace a built-in rule and disable entries. Parsing is deliberately
strict and never includes file contents or values in a problem message.

Detection is best effort: a missing finding never means a value is safe.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path

from lion.output import display_path

MAX_PREFIX_FILE_BYTES = 64 * 1024
"""Largest prefix overlay that is read, in bytes."""

DEFAULT_KIND = "prefixed-secret"
DEFAULT_MIN_TAIL = 20
DEFAULT_MAX_TAIL: int | None = None

SOURCE_BUILTIN = "builtin"
SOURCE_USER = "user"

_KIND_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
_DIGITS_PATTERN = re.compile(r"[0-9]+")


@dataclass(frozen=True, slots=True)
class PrefixRule:
    """One prefix rule without any value.

    Attributes:
        prefix: Literal prefix that starts a candidate token.
        kind: Short kind used in findings and masks.
        rule: Stable rule id such as ``provider.github_token``.
        min_tail: Minimum number of characters after the prefix.
        max_tail: Maximum number of trailing characters, or ``None``.
        source: ``builtin`` or ``user``.
        line: One-based line in the user overlay, or ``None`` for a default.
        active: Whether the rule takes part in detection.
    """

    prefix: str
    kind: str
    rule: str
    min_tail: int
    max_tail: int | None
    source: str
    line: int | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class PrefixProblem:
    """One configuration problem without the offending line content.

    Attributes:
        line: One-based line for a line problem, or ``None`` for a file problem.
        message: Stable, value-free description.
    """

    line: int | None
    message: str


@dataclass(frozen=True, slots=True)
class PrefixConfig:
    """Merged default and user prefix rules.

    Attributes:
        rules: All rules including disabled ones.
        problems: Configuration problems; an empty tuple is a valid result.
        path: Overlay path, or ``None`` for an isolated parse.
    """

    rules: tuple[PrefixRule, ...]
    problems: tuple[PrefixProblem, ...] = ()
    path: Path | None = None


def _rule(prefix: str, kind: str, rule: str, min_tail: int, max_tail: int | None) -> PrefixRule:
    """Return one built-in rule."""
    return PrefixRule(
        prefix=prefix,
        kind=kind,
        rule=rule,
        min_tail=min_tail,
        max_tail=max_tail,
        source=SOURCE_BUILTIN,
    )


DEFAULT_PREFIXES: tuple[PrefixRule, ...] = (
    _rule("AKIA", "aws-access-key", "provider.aws_access_key", 16, 16),
    _rule("ASIA", "aws-access-key", "provider.aws_access_key", 16, 16),
    _rule("ghp_", "github-token", "provider.github_token", 20, None),
    _rule("gho_", "github-token", "provider.github_token", 20, None),
    _rule("ghu_", "github-token", "provider.github_token", 20, None),
    _rule("ghs_", "github-token", "provider.github_token", 20, None),
    _rule("ghr_", "github-token", "provider.github_token", 20, None),
    _rule("glpat-", "gitlab-token", "provider.gitlab_token", 20, None),
    _rule("sk-", "openai-token", "provider.openai_token", 20, None),
    _rule("sk-proj-", "openai-token", "provider.openai_token", 40, None),
    _rule("xoxa-", "slack-token", "provider.slack_token", 10, None),
    _rule("xoxb-", "slack-token", "provider.slack_token", 10, None),
    _rule("xoxp-", "slack-token", "provider.slack_token", 10, None),
    _rule("xoxr-", "slack-token", "provider.slack_token", 10, None),
    _rule("xoxs-", "slack-token", "provider.slack_token", 10, None),
    _rule("sk_live_", "stripe-token", "provider.stripe_token", 16, None),
    _rule("rk_live_", "stripe-token", "provider.stripe_token", 16, None),
    _rule("AIza", "google-api-key", "provider.google_api_key", 35, 35),
    _rule("pypi-", "pypi-token", "provider.pypi_token", 16, None),
    _rule("npm_", "npm-token", "provider.npm_token", 36, 36),
    _rule("hf_", "huggingface-token", "provider.huggingface_token", 30, None),
    _rule("dckr_pat_", "docker-token", "provider.docker_token", 20, None),
)
"""Built-in provider prefixes, taken over from the former provider rules."""


def parse_prefixes(
    text: str,
    *,
    source: str,
    initial: tuple[PrefixRule, ...] = (),
) -> PrefixConfig:
    """Parse an overlay and merge it over ``initial``.

    Lines use ``prefix [kind] [min_tail] [max_tail]``. A line starting with
    ``!`` disables an existing prefix. Blank lines and ``#`` comments are
    ignored. The last user definition for a prefix wins.

    Args:
        text: Overlay text to parse.
        source: ``builtin`` or ``user`` for the parsed rules.
        initial: Rules already known before this overlay, usually the defaults.

    Returns:
        The merged rules and value-free problems.
    """
    merged: list[PrefixRule] = list(initial)
    by_prefix: dict[str, int] = {rule.prefix: index for index, rule in enumerate(merged)}
    problems: list[PrefixProblem] = []

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            prefix = line[1:]
            if not prefix or any(character.isspace() for character in prefix):
                problems.append(PrefixProblem(number, "invalid disable entry"))
                continue
            index = by_prefix.get(prefix)
            if index is None:
                problems.append(PrefixProblem(number, "cannot disable an unknown prefix"))
                continue
            merged[index] = replace(merged[index], active=False)
            continue

        fields = line.split()
        if len(fields) > 4:
            problems.append(PrefixProblem(number, "invalid entry: too many fields"))
            continue
        prefix = fields[0]
        if not prefix or prefix.startswith("#"):
            problems.append(PrefixProblem(number, "invalid entry: missing prefix"))
            continue
        kind = fields[1] if len(fields) >= 2 else DEFAULT_KIND
        if _KIND_PATTERN.fullmatch(kind) is None:
            problems.append(PrefixProblem(number, "invalid entry: invalid kind"))
            continue
        min_tail = DEFAULT_MIN_TAIL
        if len(fields) >= 3:
            if _DIGITS_PATTERN.fullmatch(fields[2]) is None:
                problems.append(PrefixProblem(number, "invalid entry: min_tail is not a number"))
                continue
            min_tail = int(fields[2])
            if min_tail < 1:
                problems.append(PrefixProblem(number, "invalid entry: min_tail must be positive"))
                continue
        max_tail = DEFAULT_MAX_TAIL
        if len(fields) >= 4 and fields[3] not in {"-", "none"}:
            if _DIGITS_PATTERN.fullmatch(fields[3]) is None:
                problems.append(PrefixProblem(number, "invalid entry: max_tail is not a number"))
                continue
            max_tail = int(fields[3])
            if max_tail < min_tail:
                problems.append(PrefixProblem(number, "invalid entry: max_tail is smaller than min_tail"))
                continue

        rule = PrefixRule(
            prefix=prefix,
            kind=kind,
            rule=f"prefix.{_slug(prefix)}",
            min_tail=min_tail,
            max_tail=max_tail,
            source=source,
            line=number,
        )
        index = by_prefix.get(prefix)
        if index is None:
            by_prefix[prefix] = len(merged)
            merged.append(rule)
            continue
        previous = merged[index]
        if previous.source == SOURCE_USER:
            problems.append(PrefixProblem(number, "duplicate user prefix; last entry wins"))
        merged[index] = rule

    return PrefixConfig(rules=tuple(merged), problems=tuple(problems))


def load_prefixes(path: Path) -> PrefixConfig:
    """Load defaults and an optional user overlay from ``path``.

    A missing file is not a problem. Symlinks, non-regular files, files above
    :data:`MAX_PREFIX_FILE_BYTES`, NUL bytes, invalid UTF-8 and read errors
    produce one value-free problem and fall back to the defaults.

    Args:
        path: Overlay path below ``~/.shlib``.

    Returns:
        The merged configuration.
    """
    try:
        is_symlink = path.is_symlink()
    except OSError:
        return _defaults(path, "cannot inspect the prefix file")
    if is_symlink:
        return _defaults(path, "prefix file is a symlink")
    try:
        exists = path.exists()
    except OSError:
        return _defaults(path, "cannot inspect the prefix file")
    if not exists:
        return PrefixConfig(rules=DEFAULT_PREFIXES, path=path)
    if not path.is_file():
        return _defaults(path, "prefix file is not a regular file")
    try:
        handle = path.open("rb")
    except OSError:
        return _defaults(path, "cannot read the prefix file")
    with handle:
        try:
            info = os.fstat(handle.fileno())
        except OSError:
            return _defaults(path, "cannot read the prefix file")
        if not stat.S_ISREG(info.st_mode):
            return _defaults(path, "prefix file is not a regular file")
        if info.st_size > MAX_PREFIX_FILE_BYTES:
            return _defaults(path, "prefix file is larger than 64 KiB")
        try:
            data = handle.read(MAX_PREFIX_FILE_BYTES + 1)
        except OSError:
            return _defaults(path, "cannot read the prefix file")
    if len(data) > MAX_PREFIX_FILE_BYTES:
        return _defaults(path, "prefix file is larger than 64 KiB")
    if b"\x00" in data:
        return _defaults(path, "prefix file contains NUL bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _defaults(path, "prefix file is not valid UTF-8")
    config = parse_prefixes(text, source=SOURCE_USER, initial=DEFAULT_PREFIXES)
    return replace(config, path=path)


def effective_rules(config: PrefixConfig) -> tuple[PrefixRule, ...]:
    """Return active rules with the longest prefix first.

    Args:
        config: Merged configuration.

    Returns:
        Active rules sorted by descending prefix length, then by prefix.
    """
    active = [rule for rule in config.rules if rule.active]
    return tuple(sorted(active, key=lambda rule: (-len(rule.prefix), rule.prefix)))


def effective_display(config: PrefixConfig, *, home: Path | None = None) -> tuple[str, ...]:
    """Return the lines shown by ``lion list-prefixes``.

    Args:
        config: Merged configuration.
        home: Optional home directory used to abbreviate the overlay path.

    Returns:
        Header lines followed by one line per active and disabled rule.
    """
    path = _display_path(config.path, home)
    header = f"prefixes: {path} (overlay on builtin)"
    columns = f"{'status':<9} {'prefix':<10} {'kind':<17} {'min':>3} {'max':>4}  source"
    lines = [header, columns]
    active = sorted((rule for rule in config.rules if rule.active), key=lambda rule: rule.prefix)
    disabled = sorted((rule for rule in config.rules if not rule.active), key=lambda rule: rule.prefix)
    for rule in (*active, *disabled):
        status = "active" if rule.active else "disabled"
        maximum = "-" if rule.max_tail is None else str(rule.max_tail)
        lines.append(f"{status:<9} {rule.prefix:<10} {rule.kind:<17} {rule.min_tail:>3} {maximum:>4}  {rule.source}")
    return tuple(lines)


def _slug(prefix: str) -> str:
    """Return the rule-id slug for a user prefix."""
    slug = "".join(re.findall(r"[a-z0-9]+", prefix.lower()))
    return slug or "custom"


def _defaults(path: Path, message: str) -> PrefixConfig:
    """Return the defaults with one file-level problem."""
    return PrefixConfig(rules=DEFAULT_PREFIXES, problems=(PrefixProblem(None, message),), path=path)


def _display_path(path: Path | None, home: Path | None) -> str:
    """Return the overlay path for display without resolving anything."""
    if path is None:
        return "(builtin only)"
    if home is None:
        return str(path)
    return display_path(path, home)
