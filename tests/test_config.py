"""Tests for the LION config file loader and writer (ADR 0009)."""

import stat
import tomllib
from pathlib import Path

import pytest

from lion.program.config import Config, ConfigError, config_path, load_config, write_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_config_path_uses_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``config_path`` follows ``$XDG_CONFIG_HOME`` like the rest of LION."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert config_path() == tmp_path / "cfg" / "lion" / "config.toml"


def test_config_path_falls_back_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``XDG_CONFIG_HOME`` the path is ``~/.config/lion/config.toml``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == tmp_path / ".config" / "lion" / "config.toml"


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    """A missing config is not an error and means compiled-in defaults."""
    assert load_config(tmp_path / "missing.toml") == Config(schema_version=1, watch_dog=False)


def test_load_config_uses_the_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an override the loader reads ``$XDG_CONFIG_HOME/lion/config.toml``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    path = tmp_path / "cfg" / "lion" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version = 1\n\n[watch]\ndog = true\n", encoding="utf-8")

    assert load_config().watch_dog is True


def test_valid_documents_load(tmp_path: Path) -> None:
    """The documented shapes load with their dog state."""
    assert load_config(_write(tmp_path, "schema_version = 1\n")).watch_dog is False
    assert load_config(_write(tmp_path, "schema_version = 1\n\n[watch]\n")).watch_dog is False
    assert load_config(_write(tmp_path, "schema_version = 1\n\n[watch]\ndog = true\n")).watch_dog is True
    assert load_config(_write(tmp_path, "schema_version = 1\n\n[watch]\ndog = false\n")).watch_dog is False


def test_invalid_toml_is_loud_with_path(tmp_path: Path) -> None:
    """A malformed file fails loudly and names its path."""
    path = _write(tmp_path, "schema_version = [\n")
    with pytest.raises(ConfigError, match=f"Cannot load config '{path}'"):
        load_config(path)


def test_non_utf8_file_is_loud(tmp_path: Path) -> None:
    """Undecodable bytes are a loud error, not a silent default."""
    path = tmp_path / "config.toml"
    path.write_bytes(b"schema_version = 1\n\xff\xfe\n")
    with pytest.raises(ConfigError, match="Cannot load config"):
        load_config(path)


def test_missing_schema_version_is_loud(tmp_path: Path) -> None:
    """The mandatory ``schema_version`` must be present and integer 1."""
    path = _write(tmp_path, "[watch]\ndog = true\n")
    with pytest.raises(ConfigError, match="schema_version must be 1"):
        load_config(path)


@pytest.mark.parametrize("value", ["0", "2", '"1"', "true", "1.0"])
def test_wrong_schema_version_is_loud(tmp_path: Path, value: str) -> None:
    """A wrong, non-integer or boolean schema version is rejected."""
    path = _write(tmp_path, f"schema_version = {value}\n")
    with pytest.raises(ConfigError, match="schema_version must be 1"):
        load_config(path)


def test_unknown_top_level_key_is_loud(tmp_path: Path) -> None:
    """An unknown top-level key is a loud error naming the key."""
    path = _write(tmp_path, "schema_version = 1\nfuture = true\n")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path)


def test_watch_must_be_a_table(tmp_path: Path) -> None:
    """``watch`` cannot be a scalar value."""
    path = _write(tmp_path, "schema_version = 1\nwatch = 3\n")
    with pytest.raises(ConfigError, match="watch must be a TOML table"):
        load_config(path)


def test_unknown_watch_key_is_loud(tmp_path: Path) -> None:
    """An unknown key inside ``[watch]`` is rejected."""
    path = _write(tmp_path, "schema_version = 1\n\n[watch]\nother = true\n")
    with pytest.raises(ConfigError, match=r"unknown key\(s\) in \[watch\]: other"):
        load_config(path)


@pytest.mark.parametrize("value", ['"yes"', "1", "0"])
def test_watch_dog_must_be_boolean(tmp_path: Path, value: str) -> None:
    """``watch.dog`` is strictly a boolean, never a truthy integer or string."""
    path = _write(tmp_path, f"schema_version = 1\n\n[watch]\ndog = {value}\n")
    with pytest.raises(ConfigError, match=r"watch\.dog must be a boolean"):
        load_config(path)


def test_write_config_round_trips(tmp_path: Path) -> None:
    """Written configs load back with the same values."""
    path = tmp_path / "cfg" / "lion" / "config.toml"

    assert write_config(Config(schema_version=1, watch_dog=True), path) == path
    assert load_config(path).watch_dog is True

    write_config(Config(schema_version=1, watch_dog=False), path)
    assert load_config(path).watch_dog is False


def test_write_config_is_private_and_atomic(tmp_path: Path) -> None:
    """The file appears with mode 600 and no temporary file remains."""
    path = tmp_path / "cfg" / "lion" / "config.toml"

    write_config(Config(watch_dog=True), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(child.name for child in path.parent.iterdir()) == ["config.toml"]
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "watch": {"dog": True},
    }
