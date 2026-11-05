"""Dependency-free secret detection, confidence scoring and masking.

The detector is heuristic: provider patterns and Shannon entropy produce a
confidence in percent, but that number is not a calibrated probability. A low
confidence means "not detected", never "clean".

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
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path

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
_QUOTED = re.compile(r"(?P<quote>['\"])(?P<value>[^'\"\r\n]{20,})(?P=quote)")
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<user>[^/\s:@]+):(?P<password>[^/\s@]+)@")

_PLACEHOLDER_PATTERNS = (
    re.compile(r"^changeme$", re.IGNORECASE),
    re.compile(r"^change_me$", re.IGNORECASE),
    re.compile(r"^your_.*_here$", re.IGNORECASE),
    re.compile(r"^\$\{.*\}$"),
    re.compile(r"^\{\{.*\}\}$"),
    re.compile(r"^\*+$"),
    re.compile(r"^<.*>$"),
)


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
        kind="private-key",
        rule="provider.private_key",
        pattern=re.compile(
            r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        confidence=99,
        multiline=True,
    ),
    _ProviderRule(
        kind="aws-access-key",
        rule="provider.aws_access_key",
        pattern=re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="github-token",
        rule="provider.github_token",
        pattern=re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="gitlab-token",
        rule="provider.gitlab_token",
        pattern=re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="openai-token",
        rule="provider.openai_token",
        pattern=re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="slack-token",
        rule="provider.slack_token",
        pattern=re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="stripe-token",
        rule="provider.stripe_token",
        pattern=re.compile(r"(?<![A-Za-z0-9])[rs]k_live_[A-Za-z0-9]{16,}(?![A-Za-z0-9])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="google-api-key",
        rule="provider.google_api_key",
        pattern=re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="pypi-token",
        rule="provider.pypi_token",
        pattern=re.compile(r"(?<![A-Za-z0-9_-])pypi-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="npm-token",
        rule="provider.npm_token",
        pattern=re.compile(r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
        confidence=95,
    ),
    _ProviderRule(
        kind="jwt",
        rule="provider.jwt",
        pattern=re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])"),
        confidence=95,
    ),
)


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


def find_secrets(text: str, *, key_name: str | None = None) -> tuple[SecretFinding, ...]:
    """Detect suspected secrets without storing their values.

    Args:
        text: Text to scan.
        key_name: Name of the surrounding key or file, if known.

    Returns:
        Non-overlapping findings sorted by start offset. The highest confidence
        wins an overlap; provider rules win ties against generic rules.
    """
    candidates: list[SecretFinding] = []
    candidates.extend(_provider_findings(text))
    candidates.extend(_url_findings(text))
    candidates.extend(_assignment_findings(text))
    candidates.extend(_quoted_entropy_findings(text))
    if key_name is not None and is_sensitive_name(key_name):
        sensitive = _sensitive_content_finding(text, key_name)
        if sensitive is not None:
            candidates.append(sensitive)
    specific = [finding for finding in candidates if not finding.rule.startswith("entropy.")]
    entropy = [
        finding
        for finding in candidates
        if finding.rule.startswith("entropy.") and not any(_overlaps(finding, other) for other in specific)
    ]
    return _merge_findings(specific + entropy)


def scan_file(path: Path, *, key_name: str | None = None) -> ScanResult:
    """Scan a file for secrets without reading unsafe or oversized content.

    Args:
        path: File to scan.
        key_name: Name of the surrounding key or file, if known.

    Returns:
        The findings and, for skipped files, the reason. Unreadable, binary and
        oversized files are skipped and never produce content findings.
    """
    try:
        info = path.stat()
    except OSError:
        return ScanResult(findings=(), skipped="unreadable")
    if not stat.S_ISREG(info.st_mode):
        return ScanResult(findings=(), skipped="not a regular file")
    if info.st_size > MAX_SCAN_BYTES:
        return ScanResult(findings=(), skipped="larger than 1 MiB")
    try:
        data = path.read_bytes()
    except OSError:
        return ScanResult(findings=(), skipped="unreadable")
    if b"\x00" in data:
        return ScanResult(findings=(), skipped="binary content")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ScanResult(findings=(), skipped="binary content")
    findings = find_secrets(text, key_name=key_name)
    lines = tuple(text.count("\n", 0, finding.start) + 1 for finding in findings)
    return ScanResult(findings=findings, line_numbers=lines)


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


def _assignment_findings(text: str) -> list[SecretFinding]:
    """Return findings for sensitive assignments and high-entropy values."""
    findings: list[SecretFinding] = []
    line_start = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        match = _ASSIGNMENT.match(content)
        if match is not None and not content.lstrip().startswith("#"):
            key = match.group("key")
            inner_start, inner_end = _value_span(content, match.start("value"))
            inner = content[inner_start:inner_end]
            if inner and not _is_placeholder(inner):
                start = line_start + inner_start
                end = line_start + inner_end
                if is_sensitive_name(key):
                    confidence = _sensitive_confidence(inner)
                    findings.append(
                        SecretFinding(
                            kind=_kind_for_name(key),
                            rule="assignment.sensitive_key",
                            start=start,
                            end=end,
                            confidence=confidence,
                            key_name=key,
                        )
                    )
                else:
                    confidence = _entropy_confidence(inner)
                    if confidence:
                        findings.append(
                            SecretFinding(
                                kind="high-entropy",
                                rule="entropy.assignment",
                                start=start,
                                end=end,
                                confidence=confidence,
                                key_name=key,
                            )
                        )
        line_start += len(line)
    return findings


def _quoted_entropy_findings(text: str) -> list[SecretFinding]:
    """Return entropy findings for long quoted values."""
    findings: list[SecretFinding] = []
    for match in _QUOTED.finditer(text):
        value = match.group("value")
        if _is_placeholder(value):
            continue
        confidence = _entropy_confidence(value)
        if not confidence:
            continue
        findings.append(
            SecretFinding(
                kind="high-entropy",
                rule="entropy.quoted",
                start=match.start("value"),
                end=match.end("value"),
                confidence=confidence,
            )
        )
    return findings


def _sensitive_content_finding(text: str, key_name: str) -> SecretFinding | None:
    """Return a whole-value finding for a sensitive export file name."""
    stripped = text.strip()
    if not stripped or _is_placeholder(stripped):
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


def _value_span(content: str, value_start: int) -> tuple[int, int]:
    """Return the offset span to mask inside one assignment line."""
    value = content[value_start:]
    stripped = value.lstrip()
    offset = value_start + (len(value) - len(stripped))
    if stripped[:1] in {"'", '"'}:
        quote = stripped[0]
        closing = stripped.find(quote, 1)
        if closing != -1:
            return offset + 1, offset + closing
        return offset, len(content)
    return offset, len(content)


def _entropy_confidence(value: str) -> int:
    """Return an entropy-only confidence, or ``0`` below the threshold."""
    candidate = value.strip()
    if len(candidate) < 20 or any(character.isspace() for character in candidate):
        return 0
    entropy = shannon_entropy(candidate)
    if entropy >= 4.5:
        return 80
    if entropy >= 4.0:
        return 70
    if entropy >= 3.5:
        return 55
    if entropy >= 3.0:
        return 45
    return 0


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


def _merge_findings(findings: list[SecretFinding]) -> tuple[SecretFinding, ...]:
    """Merge overlapping spans, preferring confidence and specificity."""
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
    """Return the finding whose kind and confidence should win an overlap."""
    if right.confidence > left.confidence:
        return right
    if left.confidence > right.confidence:
        return left
    left_specific = _is_specific(left)
    right_specific = _is_specific(right)
    if right_specific and not left_specific:
        return right
    if left_specific and not right_specific:
        return left
    if right.end - right.start > left.end - left.start:
        return right
    return left


def _is_specific(finding: SecretFinding) -> bool:
    """Return whether a finding comes from a provider-specific rule."""
    return finding.rule.startswith(("provider.", "url."))


def _overlaps(left: SecretFinding, right: SecretFinding) -> bool:
    """Return whether two spans overlap."""
    return left.start < right.end and right.start < left.end


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
