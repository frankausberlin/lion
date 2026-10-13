"""Tests for the prompt defaults (:mod:`lion.program.prompts`).

The shipped defaults are loaded from the package; the failure paths build a
temporary prompt folder and monkeypatch the lookup, so no test touches the
developer's environment.
"""

from pathlib import Path

import pytest

from lion.program import prompts

SHIPPED_DIR = prompts.get_prompt_dir()


def _valid_documents() -> dict[str, str]:
    """Return the shipped default documents as text, keyed by name."""
    return {name: (SHIPPED_DIR / f"{name}.md").read_text(encoding="utf-8") for name in prompts.EXPECTED_PROMPTS}


def _write(directory: Path, name: str, content: str) -> Path:
    path = directory / f"{name}.md"
    _ = path.write_text(content, encoding="utf-8")
    return path


def _broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, content: str) -> Path:
    """Write the three defaults, replacing one of them with ``content``."""
    directory = tmp_path / prompts.PROMPT_DIRNAME
    directory.mkdir()
    documents = _valid_documents()
    documents[name] = content
    for stem, text in documents.items():
        _ = _write(directory, stem, text)
    monkeypatch.setattr(prompts, "get_prompt_dir", lambda: directory)
    return directory / f"{name}.md"


# --- the shipped defaults ---------------------------------------------------


def test_shipped_defaults_load_in_order() -> None:
    """Exactly the three expected defaults load, in registration order."""
    templates = prompts.load_prompt_templates()

    assert list(templates) == list(prompts.EXPECTED_PROMPTS)
    assert templates["review_latest_changes"].arguments == (
        prompts.PromptArgumentSpec(
            name="reference",
            description="State reference to compare against; defaults to the latest stored state.",
            required=False,
        ),
        prompts.PromptArgumentSpec(
            name="focus",
            description="Optional area to focus the review on, for example 'packages' or 'network'.",
            required=False,
        ),
    )
    assert [argument.name for argument in templates["document_decision"].arguments] == ["subject", "recording"]
    assert templates["recall_knowledge"].arguments[0].required is True


def test_render_substitutes_declared_placeholders() -> None:
    """Every declared placeholder is replaced; the result is stripped."""
    template = prompts.load_prompt_templates()["recall_knowledge"]

    rendered = template.render({"query": "Why is the default browser Firefox?"})

    assert "Why is the default browser Firefox?" in rendered
    assert "{query}" not in rendered
    assert rendered == rendered.strip()


def test_render_omitted_optional_placeholder_stays_clean() -> None:
    """An omitted optional placeholder cannot leave trailing whitespace."""
    template = prompts.load_prompt_templates()["review_latest_changes"]

    rendered = template.render({"reference": "latest"})

    assert "the stored state latest" in rendered
    assert rendered == rendered.strip()


# --- folder-level failures --------------------------------------------------


def test_missing_directory_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing prompt folder is a loud error naming the path."""
    directory = tmp_path / prompts.PROMPT_DIRNAME
    monkeypatch.setattr(prompts, "get_prompt_dir", lambda: directory)

    with pytest.raises(prompts.PromptError, match=str(directory)):
        _ = prompts.load_prompt_templates()


def test_missing_file_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing expected file fails loudly with the expected path."""
    directory = tmp_path / prompts.PROMPT_DIRNAME
    directory.mkdir()
    documents = _valid_documents()
    del documents["recall_knowledge"]
    for stem, text in documents.items():
        _ = _write(directory, stem, text)
    monkeypatch.setattr(prompts, "get_prompt_dir", lambda: directory)

    with pytest.raises(prompts.PromptError, match="Missing prompt default"):
        _ = prompts.load_prompt_templates()


def test_extra_file_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A foreign Markdown file in the folder is a loud error naming it."""
    directory = tmp_path / prompts.PROMPT_DIRNAME
    directory.mkdir()
    for stem, text in _valid_documents().items():
        _ = _write(directory, stem, text)
    extra = _write(directory, "rogue", '---\nname = "rogue"\n---\n\nbody\n')
    monkeypatch.setattr(prompts, "get_prompt_dir", lambda: directory)

    with pytest.raises(prompts.PromptError, match=str(extra)):
        _ = prompts.load_prompt_templates()


# --- document-level failures ------------------------------------------------


def test_invalid_toml_frontmatter_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A frontmatter that is not valid TOML names the file."""
    path = _broken(tmp_path, monkeypatch, "recall_knowledge", "---\nname = [unterminated\n---\n\nbody\n")

    with pytest.raises(prompts.PromptError, match=str(path)):
        _ = prompts.load_prompt_templates()


def test_unknown_frontmatter_key_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixed frontmatter schema rejects unknown fields."""
    content = '---\nname = "recall_knowledge"\ndescription = "d"\nextra = "x"\n---\n\nbody\n'
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", content)

    with pytest.raises(prompts.PromptError, match="unknown frontmatter field"):
        _ = prompts.load_prompt_templates()


def test_name_must_match_the_file_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A frontmatter name that differs from the file name is a loud error."""
    content = '---\nname = "other"\ndescription = "d"\n---\n\nbody\n'
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", content)

    with pytest.raises(prompts.PromptError, match="does not match the file name"):
        _ = prompts.load_prompt_templates()


def test_duplicate_argument_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A duplicated argument name is rejected."""
    content = (
        '---\nname = "recall_knowledge"\ndescription = "d"\n'
        '[[arguments]]\nname = "query"\ndescription = "q"\nrequired = true\n'
        '[[arguments]]\nname = "query"\ndescription = "q2"\nrequired = false\n---\n\nAsk {query}.\n'
    )
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", content)

    with pytest.raises(prompts.PromptError, match="duplicate argument"):
        _ = prompts.load_prompt_templates()


def test_required_argument_must_appear_in_the_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A required argument that never appears in the body is a loud error."""
    content = (
        '---\nname = "recall_knowledge"\ndescription = "d"\n'
        '[[arguments]]\nname = "query"\ndescription = "q"\nrequired = true\n---\n\nAsk something.\n'
    )
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", content)

    with pytest.raises(prompts.PromptError, match="never appears"):
        _ = prompts.load_prompt_templates()


def test_unknown_placeholder_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A placeholder without a declared argument is a loud error."""
    content = (
        '---\nname = "recall_knowledge"\ndescription = "d"\n'
        '[[arguments]]\nname = "query"\ndescription = "q"\nrequired = true\n---\n\nAsk {query} about {topic}.\n'
    )
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", content)

    with pytest.raises(prompts.PromptError, match="not a declared argument"):
        _ = prompts.load_prompt_templates()


def test_stray_brace_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unbalanced brace in the body is rejected."""
    content = (
        '---\nname = "recall_knowledge"\ndescription = "d"\n'
        '[[arguments]]\nname = "query"\ndescription = "q"\nrequired = true\n---\n\nAsk {query} {\n'
    )
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", content)

    with pytest.raises(prompts.PromptError, match="stray"):
        _ = prompts.load_prompt_templates()


def test_empty_body_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A document without a body is rejected."""
    content = (
        '---\nname = "recall_knowledge"\ndescription = "d"\n'
        '[[arguments]]\nname = "query"\ndescription = "q"\nrequired = true\n---\n\n\n'
    )
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", content)

    with pytest.raises(prompts.PromptError, match="empty prompt body"):
        _ = prompts.load_prompt_templates()


def test_missing_frontmatter_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file without the opening fence is rejected."""
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", "Just a body.\n")

    with pytest.raises(prompts.PromptError, match="missing frontmatter"):
        _ = prompts.load_prompt_templates()


def test_unterminated_frontmatter_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file without the closing fence is rejected."""
    _ = _broken(tmp_path, monkeypatch, "recall_knowledge", '---\nname = "recall_knowledge"\n\ndescription = "d"\n')

    with pytest.raises(prompts.PromptError, match="unterminated frontmatter"):
        _ = prompts.load_prompt_templates()


def test_non_utf8_file_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that is not valid UTF-8 names the path."""
    directory = tmp_path / prompts.PROMPT_DIRNAME
    directory.mkdir()
    for stem, text in _valid_documents().items():
        _ = _write(directory, stem, text)
    _ = (directory / "recall_knowledge.md").write_bytes(b"\xff\xfe---\n")
    monkeypatch.setattr(prompts, "get_prompt_dir", lambda: directory)

    with pytest.raises(prompts.PromptError, match="not valid UTF-8"):
        _ = prompts.load_prompt_templates()
