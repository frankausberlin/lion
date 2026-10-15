"""Load and write the LION configuration file (ADR 0009).

The configuration is optional and lives at ``$XDG_CONFIG_HOME/lion/config.toml``
(fallback ``~/.config/lion/config.toml``). A missing file means built-in
defaults and is never created implicitly. A present file is validated strictly:
the ``schema_version`` must be the integer ``1``, only the documented keys are
accepted, and every violation is a loud :class:`ConfigError` naming the path.
Writing is atomic (``os.replace``, mode ``600``) and creates the parent
directories on demand; only ``write_config`` writes.
"""

import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import tomli_w

from lion.classify.rules import get_config_dir

__all__ = [
    "SCHEMA_VERSION",
    "Config",
    "ConfigError",
    "config_path",
    "load_config",
    "write_config",
]

#: Config schema version this build understands.
SCHEMA_VERSION = 1

#: The top-level keys the schema accepts.
_ALLOWED_KEYS = frozenset({"schema_version", "watch"})

#: The keys of the ``[watch]`` table the schema accepts.
_ALLOWED_WATCH_KEYS = frozenset({"dog"})


class ConfigError(ValueError):
    """The configuration file is unreadable, invalid or outside the schema."""


@dataclass(frozen=True)
class Config:
    """The validated LION configuration; defaults apply when no file exists."""

    schema_version: int = SCHEMA_VERSION
    watch_dog: bool = False


def config_path() -> Path:
    """Return the config file path (``$XDG_CONFIG_HOME/lion/config.toml``)."""
    return get_config_dir() / "config.toml"


def _invalid(path: Path, message: str) -> ConfigError:
    return ConfigError(f"Invalid config '{path}': {message}")


def _parse_document(path: Path) -> dict[str, object]:
    """Read and parse the config file, naming its path on every failure.

    The top level of a TOML document is always a table, so only syntax and
    encoding can fail here.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"Cannot load config '{path}': {exc}") from exc
    try:
        return cast("dict[str, object]", tomllib.loads(raw.decode("utf-8")))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Cannot load config '{path}': {exc}") from exc


def _parse_watch(document: dict[str, object], path: Path) -> bool:
    """Validate the optional ``[watch]`` table and return its ``dog`` state."""
    watch = document.get("watch")
    if watch is None:
        return False
    if not isinstance(watch, dict):
        raise _invalid(path, "watch must be a TOML table")
    table = cast("dict[str, object]", watch)
    extra = sorted(set(table) - _ALLOWED_WATCH_KEYS)
    if extra:
        raise _invalid(path, f"unknown key(s) in [watch]: {', '.join(extra)}")
    dog = table.get("dog")
    if dog is None:
        return False
    if not isinstance(dog, bool):
        raise _invalid(path, "watch.dog must be a boolean")
    return dog


def load_config(path: Path | None = None) -> Config:
    """Load and strictly validate the config, or return the defaults.

    A missing file yields the built-in defaults. A present but unreadable or
    invalid file is a loud :class:`ConfigError` naming the path: LION never
    partially applies a broken config.

    Args:
        path: Override the config location; defaults to :func:`config_path`.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file is unreadable, malformed, carries an unknown
            key, a wrong ``schema_version`` or a wrongly typed value.
    """
    target = config_path() if path is None else path
    if not (target.is_symlink() or target.exists()):
        return Config()
    document = _parse_document(target)
    extra = sorted(set(document) - _ALLOWED_KEYS)
    if extra:
        raise _invalid(target, f"unknown key(s): {', '.join(extra)}")
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        raise _invalid(target, f"schema_version must be {SCHEMA_VERSION}")
    return Config(schema_version=SCHEMA_VERSION, watch_dog=_parse_watch(document, target))


def write_config(config: Config, path: Path | None = None) -> Path:
    """Write the config atomically (mode ``600``) and return its path.

    The parent directories are created on demand; the file is written and
    fsynced to a temporary file in the same directory and then moved into
    place with :func:`os.replace`, so a reader never sees a partial file.

    Args:
        config: The complete configuration to persist.
        path: Override the config location; defaults to :func:`config_path`.

    Returns:
        The path of the written file.

    Raises:
        OSError: If the directory or the file cannot be written.
    """
    target = config_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {"schema_version": config.schema_version, "watch": {"dog": config.watch_dog}}
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            _ = stream.write(tomli_w.dumps(document))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
