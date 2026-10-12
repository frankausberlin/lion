"""Tests for the design report script, its metrics and its baselines."""

import json
from pathlib import Path

import pytest

from scripts import design_report

SAMPLE = '''def outer(a, b, /, c=1, *, d=2, **kw):
    """Docstring counts as a statement."""
    total = a + b + c + d
    if total > 0:
        for item in range(total):
            if item % 2:
                pass
            elif item % 3:
                continue
    else:
        total = 0

    def inner(value, *args):
        return value + 1

    return inner(total, *kw)


class Thing:
    def method(self, a, /, b, *, c):
        return a

    @staticmethod
    def static(a, b):
        return a

    @classmethod
    def klass(cls, a):
        return a
'''

IMPORT_TREE = {
    "src/lion/__init__.py": "",
    "src/lion/command/__init__.py": "",
    "src/lion/program/__init__.py": "",
    "src/lion/state/__init__.py": "",
    "src/lion/pkg/__init__.py": "",
    "src/lion/pkg/a.py": "from . import b\nimport lion.pkg.c\n",
    "src/lion/pkg/b.py": (
        "from lion.pkg import a\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from lion.pkg import c\n"
    ),
    "src/lion/pkg/c.py": "import lion.pkg.a\n",
    "src/lion/pkg/d.py": "def load():\n    from lion.pkg import a\n\n    return a\n",
    "src/lion/program/bad.py": "import typer\nfrom lion.command import x\n",
}

SMALL_MODULE = "def f(x):\n    if x:\n        return 1\n    return 0\n"


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _structure(root: Path) -> dict[str, dict[str, object]]:
    files = design_report.collect_python_files(root)
    entries = design_report.structure_entries(files, root)
    return {str(entry["symbol"]): dict(entry) for entry in entries}


def test_structure_counts_statements_depth_and_parameters(tmp_path: Path) -> None:
    """Nested definitions are separate entries; elif shares the branch level."""
    _write(tmp_path, "src/demo.py", SAMPLE)
    entries = _structure(tmp_path)
    outer = entries["outer"]
    assert outer["statements"] == 11
    assert outer["max_depth"] == 3
    assert outer["parameters"] == 4
    assert outer["has_varargs"] is False
    assert outer["has_kwargs"] is True
    inner = entries["outer.<locals>.inner"]
    assert inner["statements"] == 1
    assert inner["max_depth"] == 0
    assert inner["parameters"] == 1
    assert inner["has_varargs"] is True


def test_structure_parameters_of_methods(tmp_path: Path) -> None:
    """Receiver parameters are excluded; *args/**kwargs are reported separately."""
    _write(tmp_path, "src/demo.py", SAMPLE)
    entries = _structure(tmp_path)
    assert entries["Thing.method"]["parameters"] == 3
    assert entries["Thing.static"]["parameters"] == 2
    assert entries["Thing.klass"]["parameters"] == 1


def test_import_metrics_fan_in_cycles_and_violations(tmp_path: Path) -> None:
    """Fan-in/out, eager cycles, type-only and deferred edges are distinguished."""
    for relative, text in IMPORT_TREE.items():
        _write(tmp_path, relative, text)
    files = design_report.collect_python_files(tmp_path)
    imports = design_report.imports_metrics(files, tmp_path, {}, {})
    modules = {entry["module"]: entry for entry in imports["modules"]}
    assert modules["lion.pkg.a"]["fan_in"] == 3
    assert modules["lion.pkg.c"]["fan_in"] == 2
    assert modules["lion.pkg.a"]["fan_out"] == 2
    assert modules["lion.pkg.d"]["fan_out"] == 1
    assert imports["type_only_edges"] == 1
    assert imports["deferred_edges"] == 1
    assert len(imports["cycles"]) == 1
    assert imports["cycles"][0]["members"] == ["lion.pkg.a", "lion.pkg.b", "lion.pkg.c"]
    rules = {(violation["rule"], violation["source"], violation["target"]) for violation in imports["violations"]}
    assert ("no-typer-in-program", "lion.program.bad", "typer") in rules
    assert ("no-core-command-imports", "lion.program.bad", "lion.command") in rules
    assert "typing" in imports["external_roots"]
    assert "typer" in imports["external_roots"]


def test_cc_entries_deduplicates_class_methods() -> None:
    """Radon lists methods twice; the parser keeps one entry per symbol and line."""
    method = {"type": "method", "name": "m", "classname": "C", "lineno": 5, "complexity": 3, "rank": "A"}
    text = json.dumps(
        {
            "src/x.py": [
                dict(method),
                {
                    "type": "class",
                    "name": "C",
                    "lineno": 1,
                    "complexity": 3,
                    "rank": "A",
                    "methods": [dict(method)],
                },
            ]
        }
    )
    entries = design_report.cc_entries(text)
    assert [entry["symbol"] for entry in entries] == ["C.m"]


def test_vulture_parser_rejects_unexpected_lines() -> None:
    """Known vulture lines parse; unknown lines fail loudly."""
    line = "src/a.py:3: unused function 'x' (60% confidence)\n"
    assert design_report.vulture_entries(line) == [
        {"path": "src/a.py", "lineno": 3, "message": "unused function 'x'", "confidence": 60}
    ]
    with pytest.raises(design_report.DesignReportError):
        design_report.vulture_entries("garbage without location\n")


def test_run_publishes_report_with_real_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run publishes report.json/report.md and clears the incomplete marker."""
    _write(tmp_path, "src/demo/__init__.py", "")
    _write(tmp_path, "src/demo/mod.py", SMALL_MODULE)
    monkeypatch.chdir(tmp_path)
    assert design_report.main([]) == 0
    report = json.loads((tmp_path / ".refactor" / "report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == design_report.SCHEMA_VERSION
    assert report["complexity"]["total"] >= 1
    assert any(entry["symbol"] == "f" for entry in report["complexity"]["entries"])
    assert (tmp_path / ".refactor" / "report.md").is_file()
    assert not (tmp_path / ".refactor" / "incomplete").exists()


def test_no_scope_directory_reports_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty population is an explicit no-data result, not an error."""
    monkeypatch.chdir(tmp_path)
    assert design_report.main([]) == 0
    report = json.loads((tmp_path / ".refactor" / "report.json").read_text(encoding="utf-8"))
    assert report["complexity"]["total"] == 0
    assert report["complexity"]["median"] is None
    assert "no data" in (tmp_path / ".refactor" / "report.md").read_text(encoding="utf-8")


def test_baseline_comparison_ignores_line_shifts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unchanged symbols shifted by inserted lines are not added/removed/changed."""
    module = _write(tmp_path, "src/demo/mod.py", SMALL_MODULE)
    monkeypatch.chdir(tmp_path)
    assert design_report.main(["save", "v1"]) == 0
    module.write_text("# a comment inserted above the code\n\n\n" + SMALL_MODULE, encoding="utf-8")
    assert design_report.main(["compare", "v1"]) == 0
    report = json.loads((tmp_path / ".refactor" / "report.json").read_text(encoding="utf-8"))
    comparison = report["comparison"]
    assert comparison["functions_added"] == []
    assert comparison["functions_removed"] == []
    assert comparison["functions_changed"] == []
    assert comparison["distribution"]["A"]["delta"] == 0


def test_baseline_comparison_lists_added_and_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Added and removed functions are listed separately, without line numbers."""
    module = _write(tmp_path, "src/demo/mod.py", SMALL_MODULE)
    monkeypatch.chdir(tmp_path)
    assert design_report.main(["save", "v1"]) == 0
    module.write_text("def g(y):\n    return y\n", encoding="utf-8")
    assert design_report.main(["compare", "v1"]) == 0
    comparison = json.loads((tmp_path / ".refactor" / "report.json").read_text(encoding="utf-8"))["comparison"]
    assert comparison["functions_added"] == ["src/demo/mod.py::g"]
    assert comparison["functions_removed"] == ["src/demo/mod.py::f"]


def test_baseline_errors_are_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing, corrupt and incompatible baselines fail with exit code 1."""
    _write(tmp_path, "src/demo/mod.py", SMALL_MODULE)
    monkeypatch.chdir(tmp_path)
    assert design_report.main(["compare", "missing"]) == 1
    assert design_report.main(["save", "v1"]) == 0
    assert design_report.main(["save", "v1"]) == 1
    baseline = tmp_path / ".refactor" / "baselines" / "v1" / "report.json"
    report = json.loads(baseline.read_text(encoding="utf-8"))
    report["schema_version"] = design_report.SCHEMA_VERSION + 1
    baseline.write_text(json.dumps(report), encoding="utf-8")
    assert design_report.main(["compare", "v1"]) == 1
    baseline.write_text("not json", encoding="utf-8")
    assert design_report.main(["compare", "v1"]) == 1


def test_incomplete_marker_survives_tool_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing tool leaves the incomplete marker behind."""
    _write(tmp_path, "src/demo/mod.py", SMALL_MODULE)
    monkeypatch.chdir(tmp_path)

    def boom(mode: str, root: Path) -> str:
        raise design_report.DesignReportError(f"radon {mode} exploded")

    monkeypatch.setattr(design_report, "_run_radon", boom)
    assert design_report.main([]) == 1
    assert (tmp_path / ".refactor" / "incomplete").is_file()


def test_usage_errors_and_help() -> None:
    """Unknown commands exit 2; --help exits 0."""
    assert design_report.main(["bogus"]) == 2
    assert design_report.main(["save"]) == 2
    assert design_report.main(["save", "bad/name"]) == 2
    assert design_report.main(["--help"]) == 0
