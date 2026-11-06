"""Dependency-free secret detection, confidence scoring and masking.

The detector is best effort: provider rules, prefix rules, sensitive key names
and URL userinfo produce a confidence in percent, but that number is not a
calibrated probability. A low confidence means "not detected", never "clean".
There is no free entropy detection.

Prefix rules only match inside value regions: assignment values, shell quotes
and the whole content of an export file. A naked token such as
``cmd ghp_...`` in a shlib file is left to the optional Gitleaks stage of
``lion doctor --deep``.

Findings never store the raw value. Masking re-reads the span from the original
text only while building the replacement token. Fingerprints use HMAC-SHA256
with a per-installation random key; without a key the token has no fingerprint.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import stat
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path

from lion.secret_prefixes import DEFAULT_PREFIXES, PrefixRule

MAX_SCAN_BYTES = 1024 * 1024
"""Largest file that is scanned for secrets, in bytes."""

KEY_SIZE = 32
"""Number of random bytes in the redaction key."""

KEY_MODE = 0o600
"""Permission bits of the redaction key."""

_SENSITIVE_SINGLE = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "auth",
        "bearer",
        "credential",
        "session",
        "cookie",
        "dsn",
    }
)
_SENSITIVE_MULTI = frozenset(
    {
        "apikey",
        "accesskey",
        "privatekey",
        "clientsecret",
        "databaseurl",
    }
)
_SENSITIVE_PAIRS = frozenset(
    {
        ("api", "key"),
        ("access", "key"),
        ("private", "key"),
        ("client", "secret"),
        ("database", "url"),
    }
)

_ASSIGNMENT = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:export|set)[ \t]+)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)[ \t]*(?P<sep>=|:)[ \t]*(?P<value>[^\r\n]*)"
)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<user>[^/\s:@]+):(?P<password>[^/\s@]+)@")

_PLACEHOLDER_PATTERNS = (
    re.compile(r"^changeme$", re.IGNORECASE),
    re.compile(r"^change_me$", re.IGNORECASE),
    re.compile(r"^your_.*_here$", re.IGNORECASE),
    re.compile(r"^\$\{.*\}$"),
    re.compile(r"^\{\{.*\}\}$"),
    re.compile(r"^\*+$"),
    re.compile(r"^<.*>$"),
    re.compile(r"^x+$", re.IGNORECASE),
    re.compile(r"^0+$"),
    re.compile(r"^(?:example|dummy|fake|redacted|none|null)$", re.IGNORECASE),
)

_REFERENCE_PATTERN = re.compile(r"^[$`]")
_URL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_BOUNDARY_BEFORE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.")
_TAIL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~+/=-")

_PEM_BEGIN = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")


@dataclass(frozen=True, slots=True)
class _ProviderRule:
    """One provider-specific detection pattern."""

    kind: str
    rule: str
    pattern: re.Pattern[str]
    confidence: int
    multiline: bool = False


_PROVIDER_RULES = (
    _ProviderRule(
        kind="jwt",
        rule="provider.jwt",
        pattern=re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])"),
        confidence=95,
    ),
)
"""Complex provider rules that do not map to a simple token prefix."""


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """One suspected secret position in a text.

    Attributes:
        kind: Short kind such as ``github-token``, ``password`` or ``jwt``.
        rule: Stable rule id that produced the finding.
        start: Start offset of the span in the scanned text.
        end: End offset of the span in the scanned text.
        confidence: Heuristic confidence in percent, ``0`` to ``100``.
        key_name: Name of the surrounding key, if a key name was known.
        multiline: Whether the span covers multiple lines.
    """

    kind: str
    rule: str
    start: int
    end: int
    confidence: int
    key_name: str | None = None
    multiline: bool = False


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Result of scanning one file.

    Attributes:
        findings: Detected secrets without their raw values.
        line_numbers: One-based line number for each finding, in order.
        skipped: Reason the file was not scanned, or ``None`` when scanned.
    """

    findings: tuple[SecretFinding, ...]
    line_numbers: tuple[int, ...] = ()
    skipped: str | None = None


@dataclass(frozen=True, slots=True)
class ScanText:
    """Safely read text of one scan target.

    Attributes:
        text: Decoded text, or ``None`` when the target was skipped.
        skipped: Reason the target was not scanned, or ``None`` when read.
    """

    text: str | None
    skipped: str | None = None


@dataclass(frozen=True, slots=True)
class _ValueRegion:
    """One region where prefix candidates may appear."""

    start: int
    end: int
    key_name: str | None = None


class RedactionKeyError(Exception):
    """Raised when a redaction key exists but cannot be used safely."""


def shannon_entropy(value: str) -> float:
    """Return the Shannon entropy of ``value`` in bits per character.

    Args:
        value: Text to measure.

    Returns:
        The entropy, or ``0.0`` for an empty string.
    """
    if not value:
        return 0.0
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in Counter(value).values())


def is_sensitive_name(name: str) -> bool:
    """Return whether a key or file name looks sensitive.

    The name is split on underscores, dots and dashes so that names like
    ``GITHUB_TOKEN`` and ``db.password`` match, while unrelated words such as
    ``author`` do not match ``auth``.

    Args:
        name: Key name or file name to classify.

    Returns:
        Whether the name is in the sensitive denylist.
    """
    normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
    segments = [segment for segment in re.split(r"[^a-z0-9]+", name.lower()) if segment]
    if any(segment in _SENSITIVE_SINGLE for segment in segments):
        return True
    if normalized in _SENSITIVE_MULTI:
        return True
    if any(marker in normalized for marker in _SENSITIVE_MULTI):
        return True
    paired = set(pairwise(segments))
    return any(pair in _SENSITIVE_PAIRS for pair in paired)


def find_secrets(
    text: str,
    *,
    key_name: str | None = None,
    prefixes: tuple[PrefixRule, ...] | None = None,
) -> tuple[SecretFinding, ...]:
    """Detect suspected secrets without storing their values.

    Args:
        text: Text to scan.
        key_name: Name of the surrounding key or file, if known.
        prefixes: Active prefix rules, or the built-in defaults when ``None``.

    Returns:
        Non-overlapping findings sorted by start offset. Rule priority wins an
        overlap; at equal priority the longer span wins.
    """
    rules = DEFAULT_PREFIXES if prefixes is None else prefixes
    rules = tuple(sorted(rules, key=lambda rule: (-len(rule.prefix), rule.prefix)))
    regions = list(_value_regions(text))
    if key_name is not None:
        regions = list(_merge_regions([*regions, _ValueRegion(0, len(text), key_name)]))
    candidates: list[SecretFinding] = []
    candidates.extend(_pem_findings(text))
    candidates.extend(_provider_findings(text))
    candidates.extend(_url_findings(text))
    candidates.extend(_prefix_findings(text, rules, regions))
    candidates.extend(_assignment_findings(text))
    if key_name is not None and is_sensitive_name(key_name):
        sensitive = _sensitive_content_finding(text, key_name)
        if sensitive is not None:
            candidates.append(sensitive)
    return merge_findings(candidates)


def read_scan_text(path: Path) -> ScanText:
    """Read one scan target without following unsafe or oversized content.

    Args:
        path: File to read.

    Returns:
        The decoded text and, for skipped files, the reason. Unreadable, binary
        and oversized files are skipped and never produce content findings.
    """
    try:
        handle = path.open("rb")
    except IsADirectoryError:
        return ScanText(text=None, skipped="not a regular file")
    except OSError:
        return ScanText(text=None, skipped="unreadable")
    with handle:
        try:
            info = os.fstat(handle.fileno())
        except OSError:
            return ScanText(text=None, skipped="unreadable")
        if not stat.S_ISREG(info.st_mode):
            return ScanText(text=None, skipped="not a regular file")
        if info.st_size > MAX_SCAN_BYTES:
            return ScanText(text=None, skipped="larger than 1 MiB")
        try:
            data = handle.read(MAX_SCAN_BYTES + 1)
        except OSError:
            return ScanText(text=None, skipped="unreadable")
    if len(data) > MAX_SCAN_BYTES:
        return ScanText(text=None, skipped="larger than 1 MiB")
    if b"\x00" in data:
        return ScanText(text=None, skipped="binary content")
    try:
        return ScanText(text=data.decode("utf-8"))
    except UnicodeDecodeError:
        return ScanText(text=None, skipped="binary content")


def scan_file(
    path: Path,
    *,
    key_name: str | None = None,
    prefixes: tuple[PrefixRule, ...] | None = None,
) -> ScanResult:
    """Scan a file for secrets without reading unsafe or oversized content.

    Args:
        path: File to scan.
        key_name: Name of the surrounding key or file, if known.
        prefixes: Active prefix rules, or the built-in defaults when ``None``.

    Returns:
        The findings and, for skipped files, the reason.
    """
    result = read_scan_text(path)
    if result.text is None:
        return ScanResult(findings=(), skipped=result.skipped)
    findings = find_secrets(result.text, key_name=key_name, prefixes=prefixes)
    return ScanResult(findings=findings, line_numbers=line_numbers(result.text, findings))


def line_numbers(text: str, findings: tuple[SecretFinding, ...] | list[SecretFinding]) -> tuple[int, ...]:
    """Return one-based line numbers for findings in ``text``.

    Args:
        text: Original scanned text.
        findings: Findings with offsets into ``text``.

    Returns:
        One line number per finding, in the same order.
    """
    return tuple(text.count("\n", 0, finding.start) + 1 for finding in findings)


def mask_text(
    text: str,
    findings: tuple[SecretFinding, ...] | list[SecretFinding],
    key: bytes | None,
) -> tuple[str, bool]:
    """Replace detected spans with redaction tokens.

    Multiline spans keep their line count: the first line carries the
    fingerprint token and every further line carries a plain token.

    Args:
        text: Original text.
        findings: Non-overlapping findings from :func:`find_secrets`.
        key: HMAC key, or ``None`` to emit tokens without a fingerprint.

    Returns:
        The masked text and whether fingerprints were used.
    """
    ordered = sorted(findings, key=lambda finding: finding.start)
    pieces: list[str] = []
    cursor = 0
    fingerprinted = False
    for finding in ordered:
        if finding.start < cursor or finding.end <= finding.start:
            continue
        pieces.append(text[cursor : finding.start])
        value = text[finding.start : finding.end]
        if key is None:
            token = _plain_token(finding.kind)
        else:
            token = _fingerprint_token(finding.kind, value, key)
            fingerprinted = True
        pieces.append(_mask_value(value, finding.kind, token, multiline=finding.multiline))
        cursor = finding.end
    pieces.append(text[cursor:])
    return "".join(pieces), fingerprinted


def read_key(path: Path) -> bytes | None:
    """Read the redaction key without following or repairing an invalid path.

    Args:
        path: Key path below ``~/.shlib``.

    Returns:
        The key bytes, or ``None`` when the key does not exist.

    Raises:
        RedactionKeyError: If the path is a symlink, not a regular file, not
            exactly :data:`KEY_SIZE` bytes or cannot be read.
    """
    if path.is_symlink():
        raise RedactionKeyError(f"{path} is a symlink; shlib does not follow it")
    try:
        exists = path.exists()
    except OSError as exc:
        raise RedactionKeyError(f"cannot inspect {path}: {exc}") from exc
    if not exists:
        return None
    if not path.is_file():
        raise RedactionKeyError(f"{path} is not a regular file")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RedactionKeyError(f"cannot inspect {path}: {exc}") from exc
    if size != KEY_SIZE:
        raise RedactionKeyError(f"{path} must contain exactly {KEY_SIZE} bytes")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RedactionKeyError(f"cannot read {path}: {exc}") from exc
    if len(data) != KEY_SIZE:
        raise RedactionKeyError(f"{path} must contain exactly {KEY_SIZE} bytes")
    return data


def create_key(path: Path) -> bytes:
    """Create a new redaction key with exclusive creation.

    Args:
        path: Target key path.

    Returns:
        The generated key bytes.

    Raises:
        FileExistsError: If the path already exists.
        OSError: If the key cannot be written.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, KEY_MODE)
    data = os.urandom(KEY_SIZE)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, KEY_MODE)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return data


def fingerprint(value: str, key: bytes) -> str:
    """Return the stable, key-bound fingerprint of one secret value.

    Args:
        value: Raw secret value.
        key: Per-installation HMAC key.

    Returns:
        The first 12 hexadecimal characters of the HMAC-SHA256 digest.
    """
    return hmac.new(key, value.encode("utf-8", "surrogateescape"), hashlib.sha256).hexdigest()[:12]


def _pem_findings(text: str) -> list[SecretFinding]:
    """Return private-key blocks using a linear scan instead of a lazy regex."""
    findings: list[SecretFinding] = []
    position = 0
    while True:
        begin = _PEM_BEGIN.search(text, position)
        if begin is None:
            break
        end = _PEM_END.search(text, begin.end())
        if end is None:
            break
        findings.append(
            SecretFinding(
                kind="private-key",
                rule="provider.private_key",
                start=begin.start(),
                end=end.end(),
                confidence=99,
                multiline=True,
            )
        )
        position = end.end()
    return findings


def _provider_findings(text: str) -> list[SecretFinding]:
    """Return findings from provider-specific patterns."""
    findings: list[SecretFinding] = []
    for rule in _PROVIDER_RULES:
        for match in rule.pattern.finditer(text):
            if _is_placeholder(match.group(0)):
                continue
            findings.append(
                SecretFinding(
                    kind=rule.kind,
                    rule=rule.rule,
                    start=match.start(),
                    end=match.end(),
                    confidence=rule.confidence,
                    multiline=rule.multiline,
                )
            )
    return findings


def _url_findings(text: str) -> list[SecretFinding]:
    """Return findings for URL passwords, masking only the password."""
    findings: list[SecretFinding] = []
    for match in _URL_CREDENTIALS.finditer(text):
        password = match.group("password")
        if not password or _is_placeholder(password):
            continue
        findings.append(
            SecretFinding(
                kind="password",
                rule="url.userinfo_password",
                start=match.start("password"),
                end=match.end("password"),
                confidence=90,
            )
        )
    return findings


def _value_regions(text: str) -> tuple[_ValueRegion, ...]:
    """Return assignment values and shell-quoted regions.

    Regions are merged when they overlap or touch, so a quoted assignment value
    is scanned once. An unterminated quote extends to the end of the text; it
    must not be skipped silently.
    """
    regions: list[_ValueRegion] = []
    line_start = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = _ASSIGNMENT.match(content)
        if match is not None and not content.lstrip().startswith("#"):
            inner_start, inner_end = _value_span(content, match.start("value"))
            if inner_end > inner_start:
                regions.append(_ValueRegion(line_start + inner_start, line_start + inner_end, match.group("key")))
        line_start += len(line)

    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character not in {"'", '"'}:
            index += 1
            continue
        close = _closing_quote(text, index)
        if close == -1:
            regions.append(_ValueRegion(index + 1, len(text)))
            break
        regions.append(_ValueRegion(index + 1, close))
        index = close + 1
    return _merge_regions(regions)


def _merge_regions(regions: list[_ValueRegion]) -> tuple[_ValueRegion, ...]:
    """Merge overlapping or touching value regions."""
    ordered = sorted((region for region in regions if region.end > region.start), key=lambda region: region.start)
    merged: list[_ValueRegion] = []
    for region in ordered:
        if merged and region.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = _ValueRegion(
                start=previous.start,
                end=max(previous.end, region.end),
                key_name=previous.key_name or region.key_name,
            )
            continue
        merged.append(region)
    return tuple(merged)


def _prefix_findings(
    text: str,
    rules: tuple[PrefixRule, ...],
    regions: Sequence[_ValueRegion],
) -> list[SecretFinding]:
    """Return candidates for prefix rules inside value regions only."""
    findings: list[SecretFinding] = []
    for region in regions:
        segment = text[region.start : region.end]
        for rule in rules:
            search = 0
            while True:
                index = segment.find(rule.prefix, search)
                if index == -1:
                    break
                search = index + 1
                if index > 0 and segment[index - 1] in _BOUNDARY_BEFORE:
                    continue
                tail_start = index + len(rule.prefix)
                tail_end = tail_start
                while tail_end < len(segment) and segment[tail_end] in _TAIL_CHARS:
                    tail_end += 1
                tail = segment[tail_start:tail_end]
                if len(tail) < rule.min_tail:
                    continue
                if rule.max_tail is not None and len(tail) > rule.max_tail:
                    continue
                if len(set(tail)) <= 1:
                    continue
                candidate = rule.prefix + tail
                if _is_placeholder(candidate):
                    continue
                findings.append(
                    SecretFinding(
                        kind=rule.kind,
                        rule=rule.rule,
                        start=region.start + index,
                        end=region.start + tail_end,
                        confidence=95,
                        key_name=region.key_name,
                    )
                )
    return findings


def _assignment_findings(text: str) -> list[SecretFinding]:
    """Return findings for sensitive assignments only.

    References, paths, URLs and placeholders are not values and are skipped.
    There is no entropy fallback.
    """
    findings: list[SecretFinding] = []
    line_start = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = _ASSIGNMENT.match(content)
        if match is not None and not content.lstrip().startswith("#"):
            key = match.group("key")
            inner_start, inner_end = _value_span(content, match.start("value"))
            inner = content[inner_start:inner_end]
            if inner and is_sensitive_name(key) and not _ignored_value(inner):
                findings.append(
                    SecretFinding(
                        kind=_kind_for_name(key),
                        rule="assignment.sensitive_key",
                        start=line_start + inner_start,
                        end=line_start + inner_end,
                        confidence=_sensitive_confidence(inner),
                        key_name=key,
                    )
                )
        line_start += len(line)
    return findings


def _sensitive_content_finding(text: str, key_name: str) -> SecretFinding | None:
    """Return a whole-value finding for a sensitive export file name."""
    stripped = text.strip()
    if not stripped or _ignored_value(stripped):
        return None
    return SecretFinding(
        kind=_kind_for_name(key_name),
        rule="file.sensitive_name",
        start=0,
        end=len(text),
        confidence=_sensitive_confidence(stripped),
        key_name=key_name,
        multiline="\n" in text,
    )


def _ignored_value(value: str) -> bool:
    """Return whether a value is a placeholder, reference, path or URL."""
    candidate = value.strip().strip("'\"")
    if not candidate or _is_placeholder(candidate):
        return True
    if _REFERENCE_PATTERN.match(candidate):
        return True
    if candidate.startswith(("~", "/", "./", "../")):
        return True
    return _URL_PATTERN.match(candidate) is not None


def _value_span(content: str, value_start: int) -> tuple[int, int]:
    """Return the offset span to mask inside one assignment line."""
    value = content[value_start:]
    stripped = value.lstrip()
    offset = value_start + (len(value) - len(stripped))
    if stripped[:1] in {"'", '"'}:
        start, end = _quoted_span(stripped)
        return offset + start, offset + end
    return offset, len(content)


def _quoted_span(value: str) -> tuple[int, int]:
    """Return the inner span of contiguous shell-quoted value segments.

    Adjacent quoted segments such as ``"a""b"`` are treated as one value; the
    first and last quote are kept and everything between them is masked. A
    value that continues unquoted after the quoted part is masked to the end of
    the line, so no fragment stays visible.

    Args:
        value: Assignment value starting with a quote.

    Returns:
        Local start and end offsets for the span to mask.
    """
    segment_start = 0
    last_close = -1
    while segment_start < len(value) and value[segment_start] in {"'", '"'}:
        close = _closing_quote(value, segment_start)
        if close == -1:
            return 1, len(value)
        last_close = close
        next_index = close + 1
        if next_index < len(value) and value[next_index] in {"'", '"'}:
            segment_start = next_index
            continue
        remainder = value[next_index:].strip()
        if remainder and not remainder.startswith("#"):
            return 1, len(value)
        return 1, last_close
    return 0, len(value)


def _closing_quote(value: str, start: int) -> int:
    """Return the index of the closing quote, or ``-1`` when it is missing.

    Double-quoted shell strings treat a backslash as escaping the next
    character; single-quoted strings have no escapes.
    """
    quote = value[start]
    index = start + 1
    while index < len(value):
        if quote == '"' and value[index] == "\\":
            index += 2
            continue
        if value[index] == quote:
            return index
        index += 1
    return -1


def _sensitive_confidence(value: str) -> int:
    """Return the confidence for a sensitive key with an entropy bonus."""
    entropy = shannon_entropy(value)
    bonus = max(0, int((entropy - 3.0) * 12))
    return min(95, 70 + bonus)


def _kind_for_name(name: str) -> str:
    """Return a short password-like kind for a sensitive name."""
    normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
    if "passwd" in normalized or "password" in normalized:
        return "password"
    if "token" in normalized:
        return "token"
    if "secret" in normalized:
        return "secret"
    return "credential"


def merge_findings(findings: list[SecretFinding]) -> tuple[SecretFinding, ...]:
    """Merge overlapping spans using rule priority, length and confidence."""
    ordered = sorted(findings, key=lambda finding: (finding.start, finding.end))
    merged: list[SecretFinding] = []
    for finding in ordered:
        if finding.end <= finding.start:
            continue
        if merged and finding.start < merged[-1].end:
            current = merged[-1]
            best = _prefer(current, finding)
            start = min(current.start, finding.start)
            end = max(current.end, finding.end)
            multiline = current.multiline or finding.multiline
            key_name = best.key_name or current.key_name or finding.key_name
            merged[-1] = replace(
                best,
                start=start,
                end=end,
                multiline=multiline,
                key_name=key_name,
            )
            continue
        merged.append(finding)
    return tuple(merged)


def _prefer(left: SecretFinding, right: SecretFinding) -> SecretFinding:
    """Return the finding that should win an overlap.

    Priority order: prefix, provider and URL rules; then Gitleaks; then
    sensitive assignments and file names. At equal priority, the longer span
    wins, then the higher confidence and finally the left finding.
    """
    left_priority = _priority(left)
    right_priority = _priority(right)
    if right_priority != left_priority:
        return right if right_priority > left_priority else left
    left_length = left.end - left.start
    right_length = right.end - right.start
    if right_length != left_length:
        return right if right_length > left_length else left
    if right.confidence != left.confidence:
        return right if right.confidence > left.confidence else left
    return left


def _priority(finding: SecretFinding) -> int:
    """Return the overlap priority of one rule."""
    if finding.rule.startswith(("provider.", "prefix.", "url.")):
        return 3
    if finding.rule.startswith("gitleaks."):
        return 2
    return 1


def _is_placeholder(value: str) -> bool:
    """Return whether a value is a documented placeholder."""
    candidate = value.strip().strip("'\"")
    if not candidate:
        return True
    return any(pattern.match(candidate) is not None for pattern in _PLACEHOLDER_PATTERNS)


def _plain_token(kind: str) -> str:
    """Return the token used when no key is available."""
    return f"[REDACTED:{kind}]"


def _fingerprint_token(kind: str, value: str, key: bytes) -> str:
    """Return the token with a key-bound fingerprint."""
    return f"[REDACTED:{kind}:{fingerprint(value, key)}]"


def _mask_value(value: str, kind: str, token: str, *, multiline: bool) -> str:
    """Return the replacement for one span, preserving line count."""
    if not multiline and "\n" not in value:
        return token
    pieces = value.split("\n")
    plain = _plain_token(kind)
    masked: list[str] = []
    for index, piece in enumerate(pieces):
        if index == 0:
            masked.append(token)
        else:
            masked.append(plain if piece else "")
    return "\n".join(masked)
