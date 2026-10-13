"""Prompt defaults shipped with the package and offered by ``lion serve``.

Each prompt is one Markdown file under ``prompt_defaults/``: a fixed TOML
frontmatter block (``name``, ``description`` and an ``[[arguments]]`` list) and
a body whose ``{placeholders}`` name declared arguments. The templates are read
once when the MCP server is built; there is no hot reload and no user override
folder in v1.

A missing, extra, unreadable or invalid file is a loud :class:`PromptError`
naming the path - never a silent fallback - so a broken package fails the
server start (and the ``serve.definition`` doctor check) instead of shipping a
half-configured surface.
"""

import importlib.resources
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

__all__ = [
    "EXPECTED_PROMPTS",
    "PROMPT_DIRNAME",
    "PromptArgumentSpec",
    "PromptError",
    "PromptTemplate",
    "get_prompt_dir",
    "load_prompt_templates",
    "parse_prompt_document",
]

#: The package folder that carries the prompt defaults.
PROMPT_DIRNAME = "prompt_defaults"

#: The exactly three prompt defaults this build ships, in registration order.
EXPECTED_PROMPTS: tuple[str, ...] = ("review_latest_changes", "document_decision", "recall_knowledge")

_FRONTMATTER_KEYS = frozenset({"name", "description", "arguments"})
_ARGUMENT_KEYS = frozenset({"name", "description", "required"})
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class PromptError(ValueError):
    """A prompt default is missing, unreadable or invalid."""


@dataclass(frozen=True)
class PromptArgumentSpec:
    """One declared prompt argument from the frontmatter."""

    name: str
    description: str
    required: bool


@dataclass(frozen=True)
class PromptTemplate:
    """One parsed prompt default: metadata plus the body template."""

    name: str
    description: str
    arguments: tuple[PromptArgumentSpec, ...]
    body: str
    path: Path

    def render(self, values: Mapping[str, str]) -> str:
        """Return the body with every placeholder replaced.

        A declared argument without a value renders as an empty string. The
        result is stripped, so an omitted optional placeholder that owns a
        whole paragraph cannot leave trailing whitespace behind. Substitution
        is a single pass: a value that contains ``{name}`` is inserted
        literally and never expanded again.
        """

        def substitute(match: re.Match[str]) -> str:
            return values.get(match.group(1), "")

        return _PLACEHOLDER.sub(substitute, self.body).strip()


def get_prompt_dir() -> Path:
    """Return the package folder that carries the prompt defaults."""
    resource = importlib.resources.files("lion.program").joinpath(PROMPT_DIRNAME)
    return Path(str(resource))


def _invalid(path: Path, message: str) -> PromptError:
    return PromptError(f"Invalid prompt default: {path}: {message}")


def _split_frontmatter(raw: bytes, path: Path) -> tuple[str, str]:
    """Split the document into its TOML frontmatter and the Markdown body."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid(path, f"not valid UTF-8: {exc}") from exc
    lines = text.split("\n")
    if lines[0].strip() != "---":
        raise _invalid(path, "missing frontmatter (the file must start with '---')")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise _invalid(path, "unterminated frontmatter (no closing '---')")


def _require_str(value: object, path: Path, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(path, f"{label} must be a non-empty string")
    return value


def _parse_arguments(value: object, path: Path) -> tuple[PromptArgumentSpec, ...]:
    if not isinstance(value, list):
        raise _invalid(path, "arguments must be a list of tables")
    arguments: list[PromptArgumentSpec] = []
    seen: set[str] = set()
    for position, item in enumerate(cast("list[object]", value), start=1):
        label = f"argument {position}"
        if not isinstance(item, dict):
            raise _invalid(path, f"{label} must be a table")
        data = cast("dict[str, object]", item)
        extra = sorted(set(data) - _ARGUMENT_KEYS)
        if extra:
            raise _invalid(path, f"{label}: unknown field(s): {', '.join(extra)}")
        name = _require_str(data.get("name"), path, f"{label}: name")
        description = _require_str(data.get("description"), path, f"{label}: description")
        required = data.get("required", False)
        if not isinstance(required, bool):
            raise _invalid(path, f"{label}: required must be a boolean")
        if name in seen:
            raise _invalid(path, f"duplicate argument name '{name}'")
        seen.add(name)
        arguments.append(PromptArgumentSpec(name=name, description=description, required=required))
    return tuple(arguments)


def _validate_body(body: str, arguments: tuple[PromptArgumentSpec, ...], path: Path) -> str:
    """Return the stripped body, validating the placeholders against the arguments."""
    stripped = body.strip()
    if not stripped:
        raise _invalid(path, "empty prompt body")
    if body.count("{") != body.count("}"):
        raise _invalid(path, "stray '{' or '}' in the prompt body")
    names = {argument.name for argument in arguments}
    used: set[str] = set()
    for match in _PLACEHOLDER.finditer(body):
        placeholder = match.group(1)
        if placeholder not in names:
            known = ", ".join(argument.name for argument in arguments) or "none"
            raise _invalid(path, f"placeholder '{{{placeholder}}}' is not a declared argument (declared: {known})")
        used.add(placeholder)
    for argument in arguments:
        if argument.required and argument.name not in used:
            raise _invalid(path, f"required argument '{argument.name}' never appears in the body")
    return stripped


def parse_prompt_document(raw: bytes, path: Path) -> PromptTemplate:
    """Parse and strictly validate one prompt default document.

    Args:
        raw: The complete file content.
        path: The file path, used for validation and error messages.

    Returns:
        The parsed template.

    Raises:
        PromptError: If the frontmatter or the body violates the fixed schema,
            naming ``path``.
    """
    frontmatter, body = _split_frontmatter(raw, path)
    try:
        data = tomllib.loads(frontmatter)
    except tomllib.TOMLDecodeError as exc:
        raise _invalid(path, f"frontmatter is not valid TOML: {exc}") from exc
    extra = sorted(set(data) - _FRONTMATTER_KEYS)
    if extra:
        raise _invalid(path, f"unknown frontmatter field(s): {', '.join(extra)}")
    name = _require_str(data.get("name"), path, "name")
    if name != path.stem:
        raise _invalid(path, f"name '{name}' does not match the file name")
    description = _require_str(data.get("description"), path, "description")
    arguments = _parse_arguments(data.get("arguments"), path)
    stripped = _validate_body(body, arguments, path)
    return PromptTemplate(
        name=name,
        description=description,
        arguments=arguments,
        body=stripped,
        path=path,
    )


def load_prompt_templates() -> dict[str, PromptTemplate]:
    """Load exactly the shipped prompt defaults, once, in registration order.

    Returns:
        The templates keyed by name, ordered like :data:`EXPECTED_PROMPTS`.

    Raises:
        PromptError: If the folder is missing, holds a foreign file, is missing
            an expected file, or one file is unreadable or invalid.
    """
    directory = get_prompt_dir()
    if not directory.is_dir():
        raise PromptError(f"Prompt directory is missing: {directory}")
    paths = sorted(directory.glob("*.md"))
    names = [path.stem for path in paths]
    missing = [name for name in EXPECTED_PROMPTS if name not in names]
    if missing:
        expected = ", ".join(str(directory / f"{name}.md") for name in missing)
        raise PromptError(f"Missing prompt default(s): {expected}")
    extra = [str(path) for path in paths if path.stem not in EXPECTED_PROMPTS]
    if extra:
        raise PromptError(f"Unexpected prompt default(s): {', '.join(extra)}")
    templates: dict[str, PromptTemplate] = {}
    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise PromptError(f"Cannot read prompt default: {path}: {exc}") from exc
        templates[path.stem] = parse_prompt_document(raw, path)
    return {name: templates[name] for name in EXPECTED_PROMPTS}
