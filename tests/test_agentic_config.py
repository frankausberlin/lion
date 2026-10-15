"""Deterministic tests for the agentic QA configuration and provider settings.

No network, no model: the committed ``tests/agentic/agentic.toml`` plus the
``LION_AGENT_*`` environment overrides are the one source of truth for the
opt-in suite's provider. This module pins the strict validation (unknown keys,
wrong types, closed vocabularies), the precedence builtin default < file <
env, and the rule that the OpenRouter API key never reaches a serialized
settings object.
"""

import json
from pathlib import Path

import pytest

from tests.agentic.agent import (
    AgentError,
    AgenticConfig,
    AgenticConfigError,
    AgentSettings,
    Provider,
    load_agentic_config,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_yields_builtin_defaults(tmp_path: Path) -> None:
    """Without a config file the builtin defaults apply: OpenRouter, English, enabled."""
    config = load_agentic_config(tmp_path / "absent.toml", env={})
    assert config == AgenticConfig()
    assert config.provider.name == "openrouter"
    assert config.provider.model == "deepseek/deepseek-v4.1-flash"
    assert config.provider.base_url == "https://openrouter.ai/api/v1"
    assert config.report.enabled is True
    assert config.report.language == "english"


def test_committed_config_matches_the_builtin_defaults() -> None:
    """The committed ``tests/agentic/agentic.toml`` resolves to the builtin defaults."""
    assert load_agentic_config(env={}) == AgenticConfig()


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("schema_version = 2\n", "schema_version must be 1"),
        ('schema_version = "1"\n', "schema_version must be 1"),
        ("schema_version = 1\nbogus = true\n", "unknown key"),
        ('schema_version = 1\n[provider]\nnope = "x"\n', "unknown key"),
        ('schema_version = 1\n[report]\nnope = "x"\n', "unknown key"),
        ('schema_version = 1\n[provider]\nname = "gemini"\n', "provider.name must be one of"),
        ('schema_version = 1\n[provider]\nmodel = "   "\n', "provider.model must be a non-empty string"),
        ('schema_version = 1\n[provider]\nbase_url = ""\n', "provider.base_url must be a non-empty string"),
        ('schema_version = 1\n[provider]\nhost = ""\n', "provider.host must be a non-empty string"),
        ('schema_version = 1\n[report]\nlanguage = "french"\n', "report.language must be one of"),
        ('schema_version = 1\n[report]\nenabled = "yes"\n', "report.enabled must be a boolean"),
        ("schema_version = 1\nprovider = 3\n", "provider must be a TOML table"),
        ("schema_version = 1\nreport = true\n", "report must be a TOML table"),
        ("not toml [", "Cannot load agentic config"),
    ],
    ids=[
        "schema-version-value",
        "schema-version-type",
        "unknown-key",
        "unknown-provider-key",
        "unknown-report-key",
        "invalid-provider",
        "blank-model",
        "blank-base-url",
        "blank-host",
        "invalid-language",
        "wrong-enabled-type",
        "provider-not-a-table",
        "report-not-a-table",
        "toml-syntax",
    ],
)
def test_invalid_config_is_loud_with_the_path(tmp_path: Path, document: str, message: str) -> None:
    """Every schema violation names the config path and the offending field."""
    path = _write(tmp_path / "agentic.toml", document)
    with pytest.raises(AgenticConfigError, match=message) as excinfo:
        load_agentic_config(path, env={})
    assert str(path) in str(excinfo.value)


def test_file_values_win_over_the_builtin_defaults(tmp_path: Path) -> None:
    """A present file replaces the defaults per field without touching the others."""
    path = _write(
        tmp_path / "agentic.toml",
        """
        schema_version = 1

        [provider]
        name = "ollama"
        model = "local/model:tag"
        host = "http://127.0.0.1:9999"

        [report]
        language = "german"
        """,
    )
    config = load_agentic_config(path, env={})
    assert config.provider.name == "ollama"
    assert config.provider.model == "local/model:tag"
    assert config.provider.host == "http://127.0.0.1:9999"
    assert config.provider.base_url == "https://openrouter.ai/api/v1"
    assert config.report.enabled is True
    assert config.report.language == "german"


def test_env_overrides_win_over_the_file(tmp_path: Path) -> None:
    """The environment wins per field; untouched fields keep the file value."""
    path = _write(
        tmp_path / "agentic.toml",
        """
        schema_version = 1

        [provider]
        name = "openrouter"
        model = "openrouter/free"
        base_url = "https://example.invalid/api/v1"

        [report]
        enabled = false
        language = "english"
        """,
    )
    config = load_agentic_config(
        path,
        env={
            "LION_AGENT_PROVIDER": "ollama",
            "LION_AGENT_MODEL": "local/model:tag",
            "LION_AGENT_OLLAMA_HOST": "http://127.0.0.1:9999",
            "LION_AGENT_REPORT_LANGUAGE": "german",
        },
    )
    assert config.provider.name == "ollama"
    assert config.provider.model == "local/model:tag"
    assert config.provider.host == "http://127.0.0.1:9999"
    assert config.provider.base_url == "https://example.invalid/api/v1"
    assert config.report.enabled is False
    assert config.report.language == "german"


def test_base_url_env_override(tmp_path: Path) -> None:
    """The OpenRouter base URL is overridable for a proxy without editing the file."""
    config = load_agentic_config(tmp_path / "absent.toml", env={"LION_AGENT_BASE_URL": "https://proxy.invalid/v1"})
    assert config.provider.base_url == "https://proxy.invalid/v1"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"LION_AGENT_PROVIDER": "gemini"}, "LION_AGENT_PROVIDER must be one of"),
        ({"LION_AGENT_REPORT_LANGUAGE": "french"}, "LION_AGENT_REPORT_LANGUAGE must be one of"),
    ],
    ids=["provider", "language"],
)
def test_invalid_env_override_is_loud(tmp_path: Path, env: dict[str, str], message: str) -> None:
    """An environment override outside its closed vocabulary is a loud AgentError."""
    with pytest.raises(AgentError, match=message):
        load_agentic_config(tmp_path / "absent.toml", env=env)


def test_settings_select_the_provider_endpoint_and_key(tmp_path: Path) -> None:
    """OpenRouter takes the base URL and the key; Ollama takes the host and no key."""
    openrouter_config = load_agentic_config(tmp_path / "absent.toml", env={})
    openrouter = AgentSettings.from_config(openrouter_config, env={"OPENROUTER_API_KEY": "sk-test-key"})
    assert openrouter.provider is Provider.OPENROUTER
    assert openrouter.model == "deepseek/deepseek-v4.1-flash"
    assert openrouter.endpoint == "https://openrouter.ai/api/v1"
    assert openrouter.api_key == "sk-test-key"

    ollama_config = load_agentic_config(
        tmp_path / "absent.toml",
        env={
            "LION_AGENT_PROVIDER": "ollama",
            "LION_AGENT_MODEL": "local/model:tag",
            "LION_AGENT_OLLAMA_HOST": "http://127.0.0.1:9999",
        },
    )
    ollama = AgentSettings.from_config(ollama_config, env={"OPENROUTER_API_KEY": "sk-test-key"})
    assert ollama.provider is Provider.OLLAMA
    assert ollama.model == "local/model:tag"
    assert ollama.endpoint == "http://127.0.0.1:9999"
    assert ollama.api_key == ""


def test_sampling_env_overrides_reach_the_settings(tmp_path: Path) -> None:
    """``LION_AGENT_SEED`` and ``LION_AGENT_NUM_CTX`` keep overriding the sampling options."""
    config = load_agentic_config(tmp_path / "absent.toml", env={})
    settings = AgentSettings.from_config(config, env={"LION_AGENT_SEED": "7", "LION_AGENT_NUM_CTX": "4096"})
    assert settings.options.seed == 7
    assert settings.options.num_ctx == 4096


def test_api_key_never_reaches_the_serialized_settings(tmp_path: Path) -> None:
    """The API key lives in the settings object but never in its JSON shape."""
    config = load_agentic_config(tmp_path / "absent.toml", env={})
    settings = AgentSettings.from_config(config, env={"OPENROUTER_API_KEY": "sk-live-super-secret"})
    serialized = json.dumps(settings.as_json(), ensure_ascii=False)
    assert "sk-live-super-secret" not in serialized
    facts = settings.as_json()
    assert facts["provider"] == "openrouter"
    assert facts["model"] == settings.model
    assert facts["endpoint"] == settings.endpoint
    assert "api_key" not in facts
