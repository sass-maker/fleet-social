#!/usr/bin/env python3
"""Fail if src/mashup has an import cycle.

Uses only the standard library so the cycle gate does not depend on a
second package manager.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "mashup"
PACKAGE = "mashup"


def module_name(path: Path) -> str:
    relative = path.relative_to(SRC)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([PACKAGE, *parts]) if parts else PACKAGE


def resolve_from(current: str, module: str | None, level: int) -> str | None:
    if level == 0:
        return module
    parts = current.split(".")
    if level > len(parts):
        return None
    parent = parts[: len(parts) - level]
    if module:
        parent.append(module)
    return ".".join(parent) if parent else None


def internal_imports(path: Path, current: str) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(f"{PACKAGE}."):
                    names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = resolve_from(current, node.module, node.level)
            if resolved and (resolved == PACKAGE or resolved.startswith(f"{PACKAGE}.")):
                names.add(resolved)
    return names


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    stack: list[str] = []
    on_stack: set[str] = set()
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt not in graph:
                continue
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))
            elif node in graph.get(node, ()):
                cycles.append([node])

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return cycles


def build_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        current = module_name(path)
        graph[current] = internal_imports(path, current)
    return graph


def main() -> int:
    if not SRC.is_dir():
        print(f"missing sources: {SRC}", file=sys.stderr)
        return 2
    cycles = find_cycles(build_graph())
    if cycles:
        print("Import cycles:")
        for cycle in cycles:
            print("  " + " -> ".join(cycle + [cycle[0]]))
        return 1
    print(f"Import cycles: 0 across {len(list(SRC.rglob('*.py')))} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
