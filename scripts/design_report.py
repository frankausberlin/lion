"""Actionable, reproducible design report for the refactoring workflow.

``just rate-design`` runs this entry point. It orchestrates the advisory tools
(radon for cyclomatic complexity, maintainability and raw size; vulture for
possible unused code), measures function structure and the internal import
graph with the standard library ``ast`` module, and publishes two reports
under ``.refactor/``:

- ``report.md`` is the human-readable summary with ranked findings, locations
  and limitations;
- ``report.json`` is the versioned, machine-readable report used for baselines.

Commands (``just rate-design [run | save <name> | compare <name>]``):

- ``run`` analyzes the current tree and publishes both reports;
- ``save <name>`` does the same and snapshots the reports as a baseline under
  ``.refactor/baselines/<name>/`` (an existing baseline is never overwritten);
- ``compare <name>`` analyzes the current tree and compares it with that saved
  baseline, keyed by repository-relative path plus qualified symbol.

Exit codes: ``0`` when the report was produced (findings never fail), ``1``
for operational errors (tool failure, missing or incompatible baseline) and
``2`` for usage errors. An interrupted run leaves ``.refactor/incomplete``
behind so stale successful output is never mistaken for a fresh report. All
findings are advisory measurements, never a quality gate.
"""

import ast
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

SCHEMA_VERSION = 1
SCOPE = "src"
REFACTOR_DIR = ".refactor"
BASELINES_DIR = "baselines"
INCOMPLETE_FILE = "incomplete"
REPORT_JSON = "report.json"
REPORT_MD = "report.md"
TOP_N = 10
MAX_COMPARISON_ROWS = 10
MAX_VULTURE_ROWS = 20
VULTURE_MIN_CONFIDENCE = 60
EXCLUDED_DIRS = frozenset({"__pycache__"})
_RANKS = ("A", "B", "C", "D", "E", "F")
_BASELINE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_USAGE = "usage: design_report.py [run | save <name> | compare <name>] [-h|--help]"
_VULTURE_LINE = re.compile(r"^(?P<path>.+?):(?P<lineno>\d+): (?P<message>.*?) \((?P<confidence>\d+)% confidence\)$")

METRIC_DEFINITIONS = {
    "complexity": "radon cc per function/method; class aggregates and closures are excluded from the ranking",
    "statements": (
        "AST statements per definition; nested definitions count as one statement and are measured separately"
    ),
    "max_depth": (
        "maximum control-flow nesting; if/elif chains share one level, "
        "for/while/with/try/match add one level, comprehensions do not count"
    ),
    "parameters": (
        "positional-only + positional (receiver self/cls excluded in methods) + keyword-only; "
        "*args/**kwargs are reported separately"
    ),
    "p90": "nearest-rank percentile over the analyzed population",
}


class _UsageError(Exception):
    """Raised when the command line cannot be parsed."""


class DesignReportError(Exception):
    """An operational error that makes the report unusable."""


class _Provenance(TypedDict):
    revision: str
    dirty: bool
    branch: str


class _Scope(TypedDict):
    paths: list[str]
    excluded_dirs: list[str]
    metric_definitions: dict[str, str]


class _ComplexityEntry(TypedDict):
    path: str
    symbol: str
    lineno: int
    complexity: int
    rank: str


class _Complexity(TypedDict):
    entries: list[_ComplexityEntry]
    distribution: dict[str, int]
    total: int
    median: float | None
    p90: int | None
    maximum: int | None


class _StructureEntry(TypedDict):
    path: str
    symbol: str
    lineno: int
    statements: int
    max_depth: int
    parameters: int
    has_varargs: bool
    has_kwargs: bool


class _Structure(TypedDict):
    entries: list[_StructureEntry]


class _MiEntry(TypedDict):
    path: str
    mi: float
    rank: str


class _Maintainability(TypedDict):
    entries: list[_MiEntry]


class _SizeEntry(TypedDict):
    path: str
    loc: int
    sloc: int
    lloc: int


class _VultureEntry(TypedDict):
    path: str
    lineno: int
    message: str
    confidence: int


class _ModuleEntry(TypedDict):
    module: str
    fan_in: int
    fan_out: int


class _Violation(TypedDict):
    rule: str
    description: str
    source: str
    target: str


class _Cycle(TypedDict):
    members: list[str]
    example: list[str]


class _Imports(TypedDict):
    modules: list[_ModuleEntry]
    cycles: list[_Cycle]
    violations: list[_Violation]
    type_only_edges: int
    deferred_edges: int
    external_roots: list[str]


class _ChangedValue(TypedDict):
    key: str
    field: str
    old: int | float | str
    new: int | float | str


class _DistributionChange(TypedDict):
    baseline: int
    current: int
    delta: int


class _Comparison(TypedDict):
    baseline: str
    baseline_revision: str
    baseline_generated_at: str
    warnings: list[str]
    distribution: dict[str, _DistributionChange]
    functions_added: list[str]
    functions_removed: list[str]
    functions_changed: list[_ChangedValue]
    structure_changed: list[_ChangedValue]
    modules_added: list[str]
    modules_removed: list[str]
    fan_changed: list[_ChangedValue]
    mi_changed: list[_ChangedValue]
    vulture_added: list[str]
    vulture_removed: list[str]
    vulture_changed: list[_ChangedValue]
    violations_new: list[str]
    violations_gone: list[str]
    cycles_new: list[str]
    cycles_gone: list[str]


class _Report(TypedDict):
    schema_version: int
    generated_at: str
    provenance: _Provenance
    environment: dict[str, str]
    scope: _Scope
    complexity: _Complexity
    structure: _Structure
    maintainability: _Maintainability
    size: list[_SizeEntry]
    vulture: list[_VultureEntry]
    imports: _Imports
    comparison: _Comparison | None


@dataclass
class _ModuleGraph:
    name: str
    path: str
    is_package: bool
    internal: set[str] = field(default_factory=set)
    deferred: set[str] = field(default_factory=set)
    type_only: set[str] = field(default_factory=set)
    imports_typer: bool = False
    external_roots: set[str] = field(default_factory=set)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else "no output"


def _run_tool(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)


def _git_output(root: Path, *args: str) -> str | None:
    result = _run_tool(["git", *args], root)
    return result.stdout.strip() if result.returncode == 0 else None


def _git_info(root: Path) -> _Provenance:
    revision = _git_output(root, "rev-parse", "HEAD")
    branch = _git_output(root, "branch", "--show-current")
    status = _git_output(root, "status", "--porcelain")
    return {"revision": revision or "unknown", "dirty": bool(status), "branch": branch or "unknown"}


def _tool_version(root: Path, module: str) -> str:
    result = _run_tool([sys.executable, "-m", module, "--version"], root)
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if result.returncode == 0 and text else "unknown"


def _run_radon(mode: str, root: Path) -> str:
    result = _run_tool([sys.executable, "-m", "radon", mode, SCOPE, "--json"], root)
    if result.returncode != 0:
        raise DesignReportError(f"radon {mode} failed: {_first_line(result.stderr)}")
    return result.stdout


def _run_vulture(root: Path) -> str:
    command = [sys.executable, "-m", "vulture", SCOPE, "--min-confidence", str(VULTURE_MIN_CONFIDENCE)]
    result = _run_tool(command, root)
    if result.returncode not in {0, 3}:
        raise DesignReportError(f"vulture failed (exit {result.returncode}): {_first_line(result.stderr)}")
    return result.stdout


def _parse_json_object(text: str, tool: str) -> dict[str, object]:
    try:
        return cast("dict[str, object]", json.loads(text))
    except ValueError as exc:
        raise DesignReportError(f"{tool} produced invalid JSON: {exc}") from exc


def _mapping(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _str_field(block: Mapping[str, object], key: str, where: str) -> str:
    value = block.get(key)
    if not isinstance(value, str):
        raise DesignReportError(f"unexpected radon output for {where}: '{key}' is missing")
    return value


def _int_field(block: Mapping[str, object], key: str, where: str) -> int:
    value = block.get(key)
    if not isinstance(value, int):
        raise DesignReportError(f"unexpected tool output for {where}: '{key}' is missing")
    return value


def _cc_block(path: str, block: Mapping[str, object], prefix: str) -> list[_ComplexityEntry]:
    block_type = block.get("type")
    if block_type == "class":
        raw_methods = block.get("methods")
        if not isinstance(raw_methods, list):
            return []
        method_prefix = f"{_str_field(block, 'name', path)}."
        entries: list[_ComplexityEntry] = []
        for method in cast("list[object]", raw_methods):
            mapping = _mapping(method)
            if mapping is not None:
                entries.extend(_cc_block(path, mapping, method_prefix))
        return entries
    if block_type not in {"function", "method"}:
        return []
    symbol = _qualified_symbol(block, prefix, path)
    lineno = _int_field(block, "lineno", f"{path}:{symbol}")
    return [
        {
            "path": path,
            "symbol": symbol,
            "lineno": lineno,
            "complexity": _int_field(block, "complexity", f"{path}:{symbol}"),
            "rank": _str_field(block, "rank", f"{path}:{symbol}"),
        }
    ]


def _qualified_symbol(block: Mapping[str, object], prefix: str, path: str) -> str:
    name = _str_field(block, "name", path)
    classname = block.get("classname")
    if isinstance(classname, str) and classname:
        return f"{classname}.{name}"
    return prefix + name


def cc_entries(text: str) -> list[_ComplexityEntry]:
    """Parse ``radon cc --json`` into ranked function/method entries, deduplicated."""
    data = _parse_json_object(text, "radon cc")
    entries: dict[tuple[str, int, str], _ComplexityEntry] = {}
    for path in sorted(data):
        raw_blocks = data[path]
        if not isinstance(raw_blocks, list):
            continue
        for block in cast("list[object]", raw_blocks):
            mapping = _mapping(block)
            if mapping is None:
                continue
            for entry in _cc_block(path, mapping, ""):
                entries[(entry["path"], entry["lineno"], entry["symbol"])] = entry
    # Radon's JSON lists class methods both at the top level and inside the
    # class block; the keyed collection keeps one entry per symbol and line.
    return sorted(entries.values(), key=lambda entry: (entry["path"], entry["lineno"], entry["symbol"]))


def _mi_entries(text: str) -> list[_MiEntry]:
    data = _parse_json_object(text, "radon mi")
    entries: list[_MiEntry] = []
    for path in sorted(data):
        info = _mapping(data[path])
        if info is None:
            continue
        mi_value = info.get("mi")
        rank = info.get("rank")
        if not isinstance(mi_value, (int, float)) or not isinstance(rank, str):
            raise DesignReportError(f"unexpected radon mi output for {path}")
        entries.append({"path": path, "mi": float(mi_value), "rank": rank})
    return entries


def _size_entries(text: str) -> list[_SizeEntry]:
    data = _parse_json_object(text, "radon raw")
    entries: list[_SizeEntry] = []
    for path in sorted(data):
        info = _mapping(data[path])
        if info is None:
            continue
        entries.append(
            {
                "path": path,
                "loc": _int_field(info, "loc", f"radon raw:{path}"),
                "sloc": _int_field(info, "sloc", f"radon raw:{path}"),
                "lloc": _int_field(info, "lloc", f"radon raw:{path}"),
            }
        )
    return entries


def vulture_entries(text: str) -> list[_VultureEntry]:
    """Parse vulture's text output; unexpected lines fail loudly."""
    entries: list[_VultureEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _VULTURE_LINE.match(line)
        if match is None:
            raise DesignReportError(f"unexpected vulture output line: {line}")
        entries.append(
            {
                "path": match.group("path"),
                "lineno": int(match.group("lineno")),
                "message": match.group("message"),
                "confidence": int(match.group("confidence")),
            }
        )
    entries.sort(key=lambda entry: (entry["path"], entry["lineno"], entry["message"]))
    return entries


def collect_python_files(root: Path) -> list[Path]:
    """Return the sorted Python files under the analyzed scope."""
    scope_root = root / SCOPE
    if not scope_root.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(scope_root.rglob("*.py")):
        if not any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            files.append(path)
    return files


def _count_statements(body: Sequence[ast.stmt]) -> int:
    total = 0
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            total += 1
            continue
        if isinstance(node, ast.stmt):
            total += 1
        stack.extend(ast.iter_child_nodes(node))
    return total


def _block_depth(body: Sequence[ast.stmt], depth: int) -> int:
    highest = depth
    for stmt in body:
        highest = max(highest, _statement_depth(stmt, depth))
    return highest


def _if_depth(node: ast.If, depth: int) -> int:
    deepest = _block_depth(node.body, depth + 1)
    tail = node.orelse
    while len(tail) == 1 and isinstance(tail[0], ast.If):
        chained = tail[0]
        deepest = max(deepest, _block_depth(chained.body, depth + 1))
        tail = chained.orelse
    return max(deepest, _block_depth(tail, depth + 1))


def _statement_depth(stmt: ast.stmt, depth: int) -> int:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return depth
    if isinstance(stmt, ast.If):
        return _if_depth(stmt, depth)
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
        return max(_block_depth(stmt.body, depth + 1), _block_depth(stmt.orelse, depth + 1))
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return _block_depth(stmt.body, depth + 1)
    if isinstance(stmt, (ast.Try, ast.TryStar)):
        deepest = max(
            _block_depth(stmt.body, depth + 1),
            _block_depth(stmt.orelse, depth + 1),
            _block_depth(stmt.finalbody, depth + 1),
        )
        for handler in stmt.handlers:
            deepest = max(deepest, _block_depth(handler.body, depth + 1))
        return deepest
    if isinstance(stmt, ast.Match):
        deepest = depth
        for case in stmt.cases:
            deepest = max(deepest, _block_depth(case.body, depth + 1))
        return deepest
    return depth


def _parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool) -> tuple[int, bool, bool]:
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if is_method and positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    count = len(positional) + len(arguments.kwonlyargs)
    return count, arguments.vararg is not None, arguments.kwarg is not None


def _record_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    prefix: str,
    kind: str,
    entries: list[_StructureEntry],
) -> None:
    if not prefix:
        qualname = node.name
    elif kind == "class":
        qualname = f"{prefix}.{node.name}"
    else:
        qualname = f"{prefix}.<locals>.{node.name}"
    parameters, has_varargs, has_kwargs = _parameter_count(node, kind == "class")
    entries.append(
        {
            "path": path,
            "symbol": qualname,
            "lineno": node.lineno,
            "statements": _count_statements(node.body),
            "max_depth": _block_depth(node.body, 0),
            "parameters": parameters,
            "has_varargs": has_varargs,
            "has_kwargs": has_kwargs,
        }
    )
    _visit_block(node.body, path, qualname, "function", entries)


def _visit_block(body: Sequence[ast.stmt], path: str, prefix: str, kind: str, entries: list[_StructureEntry]) -> None:
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _record_function(stmt, path, prefix, kind, entries)
        elif isinstance(stmt, ast.ClassDef):
            class_prefix = f"{prefix}.{stmt.name}" if prefix else stmt.name
            _visit_block(stmt.body, path, class_prefix, "class", entries)
        else:
            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, ast.stmt):
                    _visit_block([child], path, prefix, kind, entries)
                elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                    _visit_block(child.body, path, prefix, kind, entries)


def structure_entries(files: Sequence[Path], root: Path) -> list[_StructureEntry]:
    """Measure statements, nesting depth and parameters per definition with ``ast``."""
    entries: list[_StructureEntry] = []
    for file in files:
        display = file.relative_to(root).as_posix()
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        except (OSError, SyntaxError) as exc:
            raise DesignReportError(f"cannot parse {display}: {exc}") from exc
        _visit_block(tree.body, display, "", "module", entries)
    entries.sort(key=lambda entry: (entry["path"], entry["lineno"], entry["symbol"]))
    return entries


def _module_identity(file: Path, root: Path) -> tuple[str, bool]:
    parts = list(file.relative_to(root).parts)
    if not parts or parts[0] != SCOPE:
        raise DesignReportError(f"{file} is outside the analyzed scope '{SCOPE}'")
    package_parts = parts[1:-1]
    is_package = parts[-1] == "__init__.py"
    if not is_package:
        package_parts = [*package_parts, Path(parts[-1]).stem]
    name = ".".join(package_parts)
    if not name:
        raise DesignReportError(f"cannot derive a module name for {file}")
    return name, is_package


def _longest_known(module_name: str, known: set[str]) -> str | None:
    parts = module_name.split(".")
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in known:
            return candidate
    return None


def _record_import(graph: _ModuleGraph, module_name: str, type_only: bool, deferred: bool, known: set[str]) -> None:
    if module_name.split(".", 1)[0] == "lion":
        target = _longest_known(module_name, known)
        if target is None or target == graph.name:
            return
        if deferred:
            graph.deferred.add(target)
        elif type_only:
            graph.type_only.add(target)
        else:
            graph.internal.add(target)
        return
    _record_external_import(graph, module_name)


def _record_external_import(graph: _ModuleGraph, module_name: str) -> None:
    root_name = module_name.split(".", 1)[0]
    if root_name == "typer":
        graph.imports_typer = True
    if root_name not in {"__future__", ""}:
        graph.external_roots.add(root_name)


def _record_import_from(
    graph: _ModuleGraph, node: ast.ImportFrom, type_only: bool, deferred: bool, known: set[str]
) -> None:
    if node.level == 0:
        module = node.module or ""
        if module != "lion" and not module.startswith("lion."):
            _record_external_import(graph, module)
            return
        base = _longest_known(module, known)
    else:
        parts = graph.name.split(".")
        if not graph.is_package:
            parts = parts[:-1]
        up = node.level - 1
        if up > len(parts):
            return
        if up:
            parts = parts[: len(parts) - up]
        if node.module:
            parts = [*parts, *node.module.split(".")]
        base = _longest_known(".".join(parts), known) if parts else None
    if base is None:
        return
    for alias in node.names:
        if alias.name == "*":
            _record_import(graph, base, type_only, deferred, known)
            continue
        target = _longest_known(f"{base}.{alias.name}", known) or base
        _record_import(graph, target, type_only, deferred, known)


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _scan_imports(tree: ast.Module, graph: _ModuleGraph, known: set[str]) -> None:
    def visit(node: ast.AST, type_only: bool, deferred: bool) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.body:
                visit(child, True, deferred)
            for child in node.orelse:
                visit(child, type_only, deferred)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Imports inside function bodies are deferred (lazy): they do not
            # execute at import time and are excluded from cycle detection.
            for child in ast.iter_child_nodes(node):
                visit(child, type_only, True)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                _record_import(graph, alias.name, type_only, deferred, known)
            return
        if isinstance(node, ast.ImportFrom):
            _record_import_from(graph, node, type_only, deferred, known)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, type_only, deferred)

    visit(tree, False, False)


def _example_cycle(members: set[str], edges: dict[str, set[str]]) -> list[str]:
    start = min(members)
    path = [start]
    visited = {start}

    def search(node: str) -> bool:
        for target in sorted(edges.get(node, set())):
            if target == start:
                path.append(start)
                return True
            if target in members and target not in visited:
                visited.add(target)
                path.append(target)
                if search(target):
                    return True
                path.pop()
        return False

    search(start)
    return path if len(path) > 1 and path[-1] == start else [*sorted(members), start]


def _find_cycles(edges: dict[str, set[str]]) -> list[_Cycle]:
    order: list[str] = []
    visited: set[str] = set()
    for start in sorted(edges):
        if start in visited:
            continue
        visited.add(start)
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(sorted(edges[start])))]
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if child not in visited and child in edges:
                    visited.add(child)
                    stack.append((child, iter(sorted(edges[child]))))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                order.append(node)
    reverse: dict[str, set[str]] = {name: set() for name in edges}
    for name, targets in edges.items():
        for target in targets:
            if target in reverse:
                reverse[target].add(name)
    assigned: set[str] = set()
    cycles: list[_Cycle] = []
    for start in reversed(order):
        if start in assigned:
            continue
        component: set[str] = set()
        todo = [start]
        assigned.add(start)
        while todo:
            node = todo.pop()
            component.add(node)
            for child in reverse[node]:
                if child not in assigned:
                    assigned.add(child)
                    todo.append(child)
        if len(component) > 1:
            cycles.append({"members": sorted(component), "example": _example_cycle(component, edges)})
    cycles.sort(key=lambda cycle: cycle["members"])
    return cycles


_ARCH_RULES = {
    "no-core-command-imports": "Core and program code must not import command or CLI modules",
    "no-typer-in-program": "Program logic must not import Typer",
}


def _architecture_findings(graphs: Sequence[_ModuleGraph]) -> list[_Violation]:
    violations: list[_Violation] = []
    for graph in graphs:
        is_core = graph.name.startswith(("lion.program", "lion.state"))
        if is_core:
            for target in sorted(graph.internal | graph.deferred | graph.type_only):
                is_command = target == "lion.command" or target.startswith("lion.command.")
                if is_command or target in {"lion.cli", "lion.main"}:
                    violations.append(
                        {
                            "rule": "no-core-command-imports",
                            "description": _ARCH_RULES["no-core-command-imports"],
                            "source": graph.name,
                            "target": target,
                        }
                    )
        if graph.name.startswith("lion.program") and graph.imports_typer:
            violations.append(
                {
                    "rule": "no-typer-in-program",
                    "description": _ARCH_RULES["no-typer-in-program"],
                    "source": graph.name,
                    "target": "typer",
                }
            )
    return violations


def imports_metrics(files: Sequence[Path], root: Path) -> _Imports:
    """Build the internal module graph, eager cycles and architecture findings."""
    graphs: list[_ModuleGraph] = []
    for file in files:
        name, is_package = _module_identity(file, root)
        graphs.append(_ModuleGraph(name=name, path=file.relative_to(root).as_posix(), is_package=is_package))
    known = {graph.name for graph in graphs}
    for file, graph in zip(files, graphs, strict=True):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        except (OSError, SyntaxError) as exc:
            raise DesignReportError(f"cannot parse {graph.path}: {exc}") from exc
        _scan_imports(tree, graph, known)
    fan_in: dict[str, int] = dict.fromkeys(known, 0)
    for graph in graphs:
        for target in graph.internal | graph.deferred | graph.type_only:
            if target in fan_in:
                fan_in[target] += 1
    eager_edges = {graph.name: set(graph.internal) for graph in graphs}
    cycles = _find_cycles(eager_edges)
    violations = _architecture_findings(graphs)
    external = sorted({root_name for graph in graphs for root_name in graph.external_roots})
    modules: list[_ModuleEntry] = []
    for graph in graphs:
        modules.append(
            {
                "module": graph.name,
                "fan_in": fan_in.get(graph.name, 0),
                "fan_out": len(graph.internal | graph.deferred | graph.type_only),
            }
        )
    modules.sort(key=lambda entry: entry["module"])
    type_only_edges = sum(len(graph.type_only) for graph in graphs)
    deferred_edges = sum(len(graph.deferred) for graph in graphs)
    return {
        "modules": modules,
        "cycles": cycles,
        "violations": violations,
        "type_only_edges": type_only_edges,
        "deferred_edges": deferred_edges,
        "external_roots": external,
    }


def _complexity_metrics(entries: list[_ComplexityEntry]) -> _Complexity:
    values = sorted(entry["complexity"] for entry in entries)
    counts = Counter(entry["rank"] for entry in entries)
    distribution = {rank: counts.get(rank, 0) for rank in _RANKS}
    if not values:
        return {
            "entries": entries,
            "distribution": distribution,
            "total": 0,
            "median": None,
            "p90": None,
            "maximum": None,
        }
    return {
        "entries": entries,
        "distribution": distribution,
        "total": len(values),
        "median": float(statistics.median(values)),
        "p90": values[math.ceil(0.9 * len(values)) - 1],
        "maximum": values[-1],
    }


def _empty_report(provenance: _Provenance, environment: dict[str, str]) -> _Report:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "provenance": provenance,
        "environment": environment,
        "scope": {
            "paths": [SCOPE],
            "excluded_dirs": sorted(EXCLUDED_DIRS),
            "metric_definitions": dict(METRIC_DEFINITIONS),
        },
        "complexity": {
            "entries": [],
            "distribution": {rank: 0 for rank in _RANKS},
            "total": 0,
            "median": None,
            "p90": None,
            "maximum": None,
        },
        "structure": {"entries": []},
        "maintainability": {"entries": []},
        "size": [],
        "vulture": [],
        "imports": {
            "modules": [],
            "cycles": [],
            "violations": [],
            "type_only_edges": 0,
            "deferred_edges": 0,
            "external_roots": [],
        },
        "comparison": None,
    }


def analyze(root: Path) -> tuple[_Report, dict[str, str]]:
    """Measure the scope and return the report plus the raw tool output files."""
    files = collect_python_files(root)
    provenance = _git_info(root)
    environment = {
        "python": platform.python_version(),
        "radon": _tool_version(root, "radon"),
        "vulture": _tool_version(root, "vulture"),
    }
    if not files:
        return _empty_report(provenance, environment), {}
    cc_text = _run_radon("cc", root)
    mi_text = _run_radon("mi", root)
    raw_text = _run_radon("raw", root)
    vulture_text = _run_vulture(root)
    complexity_entries = cc_entries(cc_text)
    size_entries = _size_entries(raw_text)
    mi_entries = _mi_entries(mi_text)
    structure = structure_entries(files, root)
    imports = imports_metrics(files, root)
    report: _Report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "provenance": provenance,
        "environment": environment,
        "scope": {
            "paths": [SCOPE],
            "excluded_dirs": sorted(EXCLUDED_DIRS),
            "metric_definitions": dict(METRIC_DEFINITIONS),
        },
        "complexity": _complexity_metrics(complexity_entries),
        "structure": {"entries": structure},
        "maintainability": {"entries": mi_entries},
        "size": size_entries,
        "vulture": vulture_entries(vulture_text),
        "imports": imports,
        "comparison": None,
    }
    raw = {
        "radon-cc.json": cc_text,
        "radon-mi.json": mi_text,
        "radon-raw.json": raw_text,
        "vulture.txt": vulture_text,
    }
    return report, raw


def _change(key: str, field: str, old: int | float | str, new: int | float | str) -> _ChangedValue:
    return {"key": key, "field": field, "old": old, "new": new}


def _set_diff(before: Mapping[str, object], after: Mapping[str, object]) -> tuple[list[str], list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    return sorted(after_keys - before_keys), sorted(before_keys - after_keys)


def _complexity_changes(
    before: Mapping[str, _ComplexityEntry], after: Mapping[str, _ComplexityEntry]
) -> list[_ChangedValue]:
    changed: list[_ChangedValue] = []
    for key in sorted(set(before) & set(after)):
        old, new = before[key], after[key]
        if old["complexity"] != new["complexity"]:
            changed.append(_change(key, "complexity", old["complexity"], new["complexity"]))
        if old["rank"] != new["rank"]:
            changed.append(_change(key, "rank", old["rank"], new["rank"]))
    return changed


def _structure_changes(
    before: Mapping[str, _StructureEntry], after: Mapping[str, _StructureEntry]
) -> list[_ChangedValue]:
    changed: list[_ChangedValue] = []
    for key in sorted(set(before) & set(after)):
        old, new = before[key], after[key]
        for field_name in ("statements", "max_depth", "parameters"):
            if old[field_name] != new[field_name]:
                changed.append(_change(key, field_name, old[field_name], new[field_name]))
    return changed


def _module_changes(before: Mapping[str, _ModuleEntry], after: Mapping[str, _ModuleEntry]) -> list[_ChangedValue]:
    changed: list[_ChangedValue] = []
    for key in sorted(set(before) & set(after)):
        old, new = before[key], after[key]
        if old["fan_in"] != new["fan_in"]:
            changed.append(_change(key, "fan_in", old["fan_in"], new["fan_in"]))
        if old["fan_out"] != new["fan_out"]:
            changed.append(_change(key, "fan_out", old["fan_out"], new["fan_out"]))
    return changed


def _mi_changes(before: Sequence[_MiEntry], after: Sequence[_MiEntry]) -> list[_ChangedValue]:
    before_by_path = {entry["path"]: entry for entry in before}
    after_by_path = {entry["path"]: entry for entry in after}
    changed: list[_ChangedValue] = []
    for key in sorted(set(before_by_path) & set(after_by_path)):
        old = round(before_by_path[key]["mi"], 2)
        new = round(after_by_path[key]["mi"], 2)
        if old != new:
            changed.append(_change(key, "mi", old, new))
    return changed


def _vulture_changes(before: Mapping[str, _VultureEntry], after: Mapping[str, _VultureEntry]) -> list[_ChangedValue]:
    changed: list[_ChangedValue] = []
    for key in sorted(set(before) & set(after)):
        if before[key]["confidence"] != after[key]["confidence"]:
            changed.append(_change(key, "confidence", before[key]["confidence"], after[key]["confidence"]))
    return changed


def _compare_reports(baseline: _Report, current: _Report, name: str) -> _Comparison:
    if baseline["schema_version"] != current["schema_version"]:
        raise DesignReportError(
            f"baseline '{name}' uses schema {baseline['schema_version']}, "
            f"current report uses {current['schema_version']}; recreate the baseline"
        )
    if baseline["scope"]["paths"] != current["scope"]["paths"]:
        raise DesignReportError(
            f"baseline '{name}' analyzed {baseline['scope']['paths']}, "
            f"current scope is {current['scope']['paths']}; refusing to compare"
        )
    warnings: list[str] = []
    for tool in sorted(current["environment"]):
        before = baseline["environment"].get(tool, "unknown")
        after = current["environment"].get(tool, "unknown")
        if before != after:
            warnings.append(
                f"{tool} version differs (baseline {before}, current {after}); deltas may not be comparable"
            )
    if baseline["provenance"]["dirty"]:
        warnings.append("baseline was generated from a dirty working tree")

    baseline_cc = {f"{entry['path']}::{entry['symbol']}": entry for entry in baseline["complexity"]["entries"]}
    current_cc = {f"{entry['path']}::{entry['symbol']}": entry for entry in current["complexity"]["entries"]}
    functions_added, functions_removed = _set_diff(baseline_cc, current_cc)
    functions_changed = _complexity_changes(baseline_cc, current_cc)

    baseline_structure = {f"{entry['path']}::{entry['symbol']}": entry for entry in baseline["structure"]["entries"]}
    current_structure = {f"{entry['path']}::{entry['symbol']}": entry for entry in current["structure"]["entries"]}
    structure_changed = _structure_changes(baseline_structure, current_structure)

    baseline_modules = {entry["module"]: entry for entry in baseline["imports"]["modules"]}
    current_modules = {entry["module"]: entry for entry in current["imports"]["modules"]}
    modules_added, modules_removed = _set_diff(baseline_modules, current_modules)
    fan_changed = _module_changes(baseline_modules, current_modules)

    mi_changed = _mi_changes(baseline["maintainability"]["entries"], current["maintainability"]["entries"])

    baseline_vulture = {f"{entry['path']}::{entry['message']}": entry for entry in baseline["vulture"]}
    current_vulture = {f"{entry['path']}::{entry['message']}": entry for entry in current["vulture"]}
    vulture_added, vulture_removed = _set_diff(baseline_vulture, current_vulture)
    vulture_changed = _vulture_changes(baseline_vulture, current_vulture)

    baseline_violations = {
        f"{violation['rule']}: {violation['source']} -> {violation['target']}": violation
        for violation in baseline["imports"]["violations"]
    }
    current_violations = {
        f"{violation['rule']}: {violation['source']} -> {violation['target']}": violation
        for violation in current["imports"]["violations"]
    }
    violations_new, violations_gone = _set_diff(baseline_violations, current_violations)

    baseline_cycles = {"; ".join(cycle["members"]): cycle for cycle in baseline["imports"]["cycles"]}
    current_cycles = {"; ".join(cycle["members"]): cycle for cycle in current["imports"]["cycles"]}
    cycles_new, cycles_gone = _set_diff(baseline_cycles, current_cycles)

    distribution: dict[str, _DistributionChange] = {}
    for rank in _RANKS:
        before_count = baseline["complexity"]["distribution"].get(rank, 0)
        after_count = current["complexity"]["distribution"].get(rank, 0)
        distribution[rank] = {"baseline": before_count, "current": after_count, "delta": after_count - before_count}

    return {
        "baseline": name,
        "baseline_revision": baseline["provenance"]["revision"],
        "baseline_generated_at": baseline["generated_at"],
        "warnings": warnings,
        "distribution": distribution,
        "functions_added": functions_added,
        "functions_removed": functions_removed,
        "functions_changed": functions_changed,
        "structure_changed": structure_changed,
        "modules_added": modules_added,
        "modules_removed": modules_removed,
        "fan_changed": fan_changed,
        "mi_changed": mi_changed,
        "vulture_added": vulture_added,
        "vulture_removed": vulture_removed,
        "vulture_changed": vulture_changed,
        "violations_new": violations_new,
        "violations_gone": violations_gone,
        "cycles_new": cycles_new,
        "cycles_gone": cycles_gone,
    }


def _fmt_number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _capped(items: Sequence[str]) -> str:
    shown = [f"`{item}`" for item in items[:MAX_COMPARISON_ROWS]]
    suffix = f" (+{len(items) - MAX_COMPARISON_ROWS} more)" if len(items) > MAX_COMPARISON_ROWS else ""
    return ", ".join(shown) + suffix if shown else "none"


def _changed_table(rows: Sequence[_ChangedValue]) -> list[str]:
    if not rows:
        return []
    table_rows = [
        [f"`{row['key']}`", row["field"], str(row["old"]), str(row["new"])] for row in rows[:MAX_COMPARISON_ROWS]
    ]
    lines = _table(("key", "field", "baseline", "current"), table_rows)
    if len(rows) > MAX_COMPARISON_ROWS:
        lines.append(f"({len(rows) - MAX_COMPARISON_ROWS} more changed entries in report.json)")
    lines.append("")
    return lines


def _render_comparison(comparison: _Comparison) -> list[str]:
    lines = [
        f"## Baseline comparison vs '{comparison['baseline']}'",
        "",
        f"- baseline revision: `{comparison['baseline_revision']}` · generated: {comparison['baseline_generated_at']}",
    ]
    for warning in comparison["warnings"]:
        lines.append(f"- warning: {warning}")
    lines += [
        "",
        "Compared by repository-relative path plus qualified symbol; "
        "line-number shifts alone are never added or removed symbols.",
        "",
    ]
    distribution_rows = [
        [rank, str(change["baseline"]), str(change["current"]), f"{change['delta']:+d}"]
        for rank, change in comparison["distribution"].items()
    ]
    lines += [*_table(("rank", "baseline", "current", "delta"), distribution_rows), ""]
    lines.append(
        f"Functions: {len(comparison['functions_added'])} added · "
        f"{len(comparison['functions_removed'])} removed · {len(comparison['functions_changed'])} changed."
    )
    lines += [
        "",
        f"- added: {_capped(comparison['functions_added'])}",
        f"- removed: {_capped(comparison['functions_removed'])}",
        "",
    ]
    lines += _changed_table(comparison["functions_changed"])
    lines += _changed_table(comparison["structure_changed"])
    lines.append(f"Modules: {len(comparison['modules_added'])} added · {len(comparison['modules_removed'])} removed.")
    lines += [
        "",
        f"- added: {_capped(comparison['modules_added'])}",
        f"- removed: {_capped(comparison['modules_removed'])}",
        "",
    ]
    lines += _changed_table(comparison["fan_changed"])
    lines += _changed_table(comparison["mi_changed"])
    lines.append(
        f"Vulture candidates: {len(comparison['vulture_added'])} new · {len(comparison['vulture_removed'])} gone."
    )
    lines += [
        "",
        f"- new: {_capped(comparison['vulture_added'])}",
        f"- gone: {_capped(comparison['vulture_removed'])}",
        "",
    ]
    lines += _changed_table(comparison["vulture_changed"])
    lines.append(
        f"Architecture findings: {len(comparison['violations_new'])} new · "
        f"{len(comparison['violations_gone'])} resolved."
    )
    lines += [
        "",
        f"- new: {_capped(comparison['violations_new'])}",
        f"- resolved: {_capped(comparison['violations_gone'])}",
        "",
    ]
    lines.append(f"Dependency cycles: {len(comparison['cycles_new'])} new · {len(comparison['cycles_gone'])} resolved.")
    lines += [
        "",
        f"- new: {_capped(comparison['cycles_new'])}",
        f"- resolved: {_capped(comparison['cycles_gone'])}",
        "",
    ]
    return lines


def _render_complexity(report: _Report) -> list[str]:
    metrics = report["complexity"]
    entries = metrics["entries"]
    lines = ["## Complexity (radon cc, functions and methods)", ""]
    if not entries:
        lines += ["No analyzable functions or methods were found; no data.", ""]
        return lines
    top = sorted(entries, key=lambda entry: (-entry["complexity"], entry["path"], entry["symbol"]))[:TOP_N]
    rows = [
        [
            str(index),
            f"`{entry['symbol']}`",
            f"`{entry['path']}:{entry['lineno']}`",
            str(entry["complexity"]),
            entry["rank"],
        ]
        for index, entry in enumerate(top, start=1)
    ]
    lines += [
        "Top functions/methods by cyclomatic complexity:",
        "",
        *_table(("#", "symbol", "location", "cc", "rank"), rows),
        "",
    ]
    p90 = metrics["p90"] if metrics["p90"] is not None else "n/a"
    maximum = metrics["maximum"] if metrics["maximum"] is not None else "n/a"
    lines += [
        f"Distribution over {metrics['total']} analyzed functions/methods: "
        f"median {_fmt_number(metrics['median'])} · P90 (nearest rank) {p90} · max {maximum}",
        "",
        *_table(("rank", "count"), [[rank, str(metrics["distribution"].get(rank, 0))] for rank in _RANKS]),
        "",
    ]
    return lines


def _render_structure(report: _Report) -> list[str]:
    entries = report["structure"]["entries"]
    lines = ["## Function structure (AST metrics)", ""]
    if not entries:
        lines += ["No analyzable functions or methods were found; no data.", ""]
        return lines
    statement_rows = [
        [str(index), f"`{entry['symbol']}`", f"`{entry['path']}:{entry['lineno']}`", str(entry["statements"])]
        for index, entry in enumerate(
            sorted(entries, key=lambda entry: (-entry["statements"], entry["path"], entry["symbol"]))[:TOP_N], start=1
        )
    ]
    lines += [
        "Largest by executable statements:",
        "",
        *_table(("#", "symbol", "location", "statements"), statement_rows),
        "",
    ]
    depth_rows = [
        [str(index), f"`{entry['symbol']}`", f"`{entry['path']}:{entry['lineno']}`", str(entry["max_depth"])]
        for index, entry in enumerate(
            sorted(entries, key=lambda entry: (-entry["max_depth"], entry["path"], entry["symbol"]))[:TOP_N], start=1
        )
    ]
    lines += [
        "Deepest control flow:",
        "",
        *_table(("#", "symbol", "location", "max depth"), depth_rows),
        "",
    ]
    parameter_rows = [
        [str(index), f"`{entry['symbol']}`", f"`{entry['path']}:{entry['lineno']}`", str(entry["parameters"])]
        for index, entry in enumerate(
            sorted(entries, key=lambda entry: (-entry["parameters"], entry["path"], entry["symbol"]))[:TOP_N], start=1
        )
    ]
    lines += [
        "Most parameters (excluding *args/**kwargs):",
        "",
        *_table(("#", "symbol", "location", "params"), parameter_rows),
        "",
    ]
    lines += ["Metric definitions:", ""]
    for name, definition in report["scope"]["metric_definitions"].items():
        lines.append(f"- `{name}`: {definition}")
    lines.append("")
    return lines


def _render_imports(report: _Report) -> list[str]:
    imports = report["imports"]
    modules = imports["modules"]
    lines = ["## Internal dependencies (import graph)", ""]
    if not modules:
        lines += ["No internal modules were found; no data.", ""]
    else:
        fan_in_rows = [
            [f"`{entry['module']}`", str(entry["fan_in"]), str(entry["fan_out"])]
            for entry in sorted(modules, key=lambda entry: (-entry["fan_in"], entry["module"]))[:TOP_N]
        ]
        fan_out_rows = [
            [f"`{entry['module']}`", str(entry["fan_out"]), str(entry["fan_in"])]
            for entry in sorted(modules, key=lambda entry: (-entry["fan_out"], entry["module"]))[:TOP_N]
        ]
        lines += ["Most imported modules:", "", *_table(("module", "fan-in", "fan-out"), fan_in_rows), ""]
        lines += ["Biggest importers:", "", *_table(("module", "fan-out", "fan-in"), fan_out_rows), ""]
    if imports["cycles"]:
        lines += ["Dependency cycles (advisory):", ""]
        for cycle in imports["cycles"]:
            members = ", ".join(f"`{member}`" for member in cycle["members"])
            example = " -> ".join(f"`{module}`" for module in cycle["example"])
            lines.append(f"- members: {members} · example: {example}")
        lines.append("")
    else:
        lines += ["Dependency cycles: none.", ""]
    if imports["violations"]:
        lines += ["Advisory architecture findings:", ""]
        for violation in imports["violations"]:
            lines.append(
                f"- [{violation['rule']}] {violation['source']} -> {violation['target']} — {violation['description']}"
            )
        lines.append("")
    else:
        lines += ["Advisory architecture findings: none.", ""]
    external = ", ".join(imports["external_roots"]) if imports["external_roots"] else "none"
    lines += [
        f"External top-level packages referenced in scope ({len(imports['external_roots'])}): {external}.",
        f"TYPE_CHECKING import edges counted: {imports['type_only_edges']} · "
        f"deferred (function-body) import edges counted: {imports['deferred_edges']}.",
        "Dependency cycles are derived from eager (module-level) imports only.",
        "",
    ]
    return lines


def _render_vulture(report: _Report) -> list[str]:
    entries = report["vulture"]
    lines = ["## Possible unused code (vulture, advisory — not proof of dead code)", ""]
    if not entries:
        lines += ["No vulture candidates at the configured confidence.", ""]
        return lines
    ordered = sorted(entries, key=lambda entry: (-entry["confidence"], entry["path"], entry["lineno"]))
    rows = [
        [f"`{entry['path']}:{entry['lineno']}`", entry["message"], f"{entry['confidence']}%"]
        for entry in ordered[:MAX_VULTURE_ROWS]
    ]
    lines += [*_table(("location", "message", "confidence"), rows)]
    if len(ordered) > MAX_VULTURE_ROWS:
        lines.append(f"({len(ordered) - MAX_VULTURE_ROWS} more candidates in .refactor/vulture.txt)")
    lines.append("")
    return lines


def _render_maintainability(report: _Report) -> list[str]:
    entries = report["maintainability"]["entries"]
    lines = ["## Maintainability index (radon mi, secondary context — not a verdict)", ""]
    if not entries:
        lines += ["No data.", ""]
        return lines
    rows = [
        [f"`{entry['path']}`", f"{entry['mi']:.1f}", entry["rank"]]
        for entry in sorted(entries, key=lambda entry: (entry["mi"], entry["path"]))[:TOP_N]
    ]
    lines += [*_table(("module", "mi", "rank"), rows), ""]
    return lines


def _render_size(report: _Report) -> list[str]:
    entries = report["size"]
    lines = ["## Module size (radon raw)", ""]
    if not entries:
        lines += ["No data.", ""]
        return lines
    rows = [
        [f"`{entry['path']}`", str(entry["sloc"]), str(entry["lloc"]), str(entry["loc"])]
        for entry in sorted(entries, key=lambda entry: (-entry["sloc"], entry["path"]))[:TOP_N]
    ]
    lines += ["sloc = source lines · lloc = logical lines (statements) · loc = all lines.", ""]
    lines += [*_table(("module", "sloc", "lloc", "loc"), rows), ""]
    return lines


def _render_limitations(report: _Report) -> list[str]:
    paths = ", ".join(report["scope"]["paths"])
    return [
        "## Analysis coverage and limitations",
        "",
        f"- Scope: {paths}; tests, developer scripts and generated files are not analyzed in this iteration.",
        "- radon class aggregates and closures are excluded from the ranking so symbols keep stable comparison keys.",
        "- Metrics are static: dynamic imports (importlib) are invisible; TYPE_CHECKING imports are "
        "counted, deferred imports inside function bodies are counted but excluded from cycle detection.",
        "- Shell/zsh code embedded in this project is not measured by Python AST metrics.",
        "- Raw tool output: `.refactor/radon-cc.json`, `.refactor/radon-mi.json`, "
        "`.refactor/radon-raw.json`, `.refactor/vulture.txt`.",
        "- Findings are advisory measurements, never a gate; the report exits successfully even when it "
        "lists findings.",
        "",
    ]


def _render_markdown(report: _Report) -> str:
    provenance = report["provenance"]
    environment = report["environment"]
    scope = report["scope"]
    dirty = "yes" if provenance["dirty"] else "no"
    excluded = ", ".join(scope["excluded_dirs"]) if scope["excluded_dirs"] else "none"
    python_version = environment.get("python", "unknown")
    radon_version = environment.get("radon", "unknown")
    vulture_version = environment.get("vulture", "unknown")
    scope_paths = ", ".join(f"`{path}`" for path in scope["paths"])
    lines = [
        "# Design report",
        "",
        f"- revision: `{provenance['revision']}` (dirty: {dirty}) · branch `{provenance['branch']}`",
        f"- generated: {report['generated_at']}",
        f"- python: {python_version} · radon: {radon_version} · vulture: {vulture_version}",
        f"- schema: {report['schema_version']} · scope: {scope_paths} (excluded dirs: {excluded})",
        "",
    ]
    if report["comparison"] is not None:
        lines += _render_comparison(report["comparison"])
    lines += _render_complexity(report)
    lines += _render_structure(report)
    lines += _render_imports(report)
    lines += _render_vulture(report)
    lines += _render_maintainability(report)
    lines += _render_size(report)
    lines += _render_limitations(report)
    return "\n".join(lines) + "\n"


def _load_baseline(refactor_dir: Path, name: str) -> _Report:
    path = refactor_dir / BASELINES_DIR / name / REPORT_JSON
    if not path.is_file():
        raise DesignReportError(f"baseline '{name}' not found at {path}; create it with 'just rate-design save {name}'")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DesignReportError(f"cannot read baseline '{name}' at {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise DesignReportError(f"baseline '{name}' is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DesignReportError(f"baseline '{name}' is not a JSON object")
    mapping = cast("dict[str, object]", data)
    version = mapping.get("schema_version")
    if not isinstance(version, int):
        raise DesignReportError(f"baseline '{name}' has no integer schema_version")
    for key in (
        "generated_at",
        "provenance",
        "environment",
        "scope",
        "complexity",
        "structure",
        "maintainability",
        "size",
        "vulture",
        "imports",
    ):
        if key not in mapping:
            raise DesignReportError(f"baseline '{name}' is missing '{key}'")
    return cast("_Report", mapping)


def _snapshot_baseline(refactor_dir: Path, name: str) -> Path:
    target = refactor_dir / BASELINES_DIR / name
    if target.exists():
        raise DesignReportError(f"baseline '{name}' already exists at {target}; refusing to overwrite")
    target.mkdir(parents=True)
    for file_name in (REPORT_JSON, REPORT_MD):
        _write_atomic(target / file_name, (refactor_dir / file_name).read_text(encoding="utf-8"))
    return target


def _execute(command: str, name: str | None, root: Path) -> Path:
    refactor_dir = root / REFACTOR_DIR
    refactor_dir.mkdir(parents=True, exist_ok=True)
    marker = refactor_dir / INCOMPLETE_FILE
    _write_atomic(marker, json.dumps({"started_at": _now_iso(), "command": command, "baseline": name}) + "\n")
    report, raw = analyze(root)
    if command == "compare":
        if name is None:
            raise DesignReportError("compare requires a baseline name")
        baseline = _load_baseline(refactor_dir, name)
        report["comparison"] = _compare_reports(baseline, report, name)
    for file_name, text in raw.items():
        _write_atomic(refactor_dir / file_name, text)
    _write_atomic(refactor_dir / REPORT_JSON, json.dumps(report, indent=2) + "\n")
    _write_atomic(refactor_dir / REPORT_MD, _render_markdown(report))
    if command == "save":
        if name is None:
            raise DesignReportError("save requires a baseline name")
        _snapshot_baseline(refactor_dir, name)
    marker.unlink(missing_ok=True)
    return refactor_dir / REPORT_MD


def _parse_argv(argv: Sequence[str]) -> tuple[str, str | None]:
    args = list(argv)
    if not args or args == ["run"]:
        return "run", None
    if len(args) == 1 and args[0] in {"-h", "--help"}:
        return "help", None
    if len(args) == 2 and args[0] in {"save", "compare"} and _BASELINE_NAME_RE.fullmatch(args[1]):
        return args[0], args[1]
    raise _UsageError(_USAGE)


def main(argv: Sequence[str]) -> int:
    """Run the design report CLI and return the process exit code."""
    try:
        command, name = _parse_argv(argv)
    except _UsageError as exc:
        print(f"design-report: {exc}", file=sys.stderr)
        return 2
    if command == "help":
        print(_USAGE)
        return 0
    root = Path.cwd()
    try:
        report_path = _execute(command, name, root)
    except (DesignReportError, OSError) as exc:
        print(f"design-report: {exc}", file=sys.stderr)
        return 1
    display = report_path.relative_to(root).as_posix()
    summary = f"Report: {display}"
    if command == "save" and name is not None:
        summary += f" · Baseline: {REFACTOR_DIR}/{BASELINES_DIR}/{name}/ (never overwritten)"
    elif command == "compare" and name is not None:
        summary += f" · compared against baseline '{name}'"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
