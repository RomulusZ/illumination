"""Validate Illumination's internal Python import boundaries."""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "illumination"
LAYERS = ("types", "config", "repo", "service", "runtime", "ui")
ALLOWED_INTERNAL_PREFIXES: dict[str, tuple[str, ...]] = {
    "types": ("illumination.types",),
    "config": ("illumination.types", "illumination.config"),
    "repo": ("illumination.types", "illumination.config", "illumination.repo"),
    "service": ("illumination.types", "illumination.config", "illumination.service"),
    "runtime": (
        "illumination.types",
        "illumination.config",
        "illumination.repo",
        "illumination.service",
        "illumination.runtime",
    ),
    "ui": (
        "illumination.types",
        "illumination.config",
        "illumination.service",
        "illumination.runtime",
        "illumination.ui",
    ),
}
EXEMPT_PARTS = frozenset({"tests", "scripts", "lint", "__pycache__"})
DYNAMIC_IMPORT_NAMES = frozenset({"import_module", "__import__"})


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


@dataclass(frozen=True, slots=True)
class ImportReference:
    module: str
    line: int


def _package_index(parts: tuple[str, ...]) -> int | None:
    """Locate the package directory, preferring the one directly under ``src``."""
    candidates = [index for index, part in enumerate(parts) if part == PACKAGE]
    if not candidates:
        return None
    for index in candidates:
        if index > 0 and parts[index - 1] == "src":
            return index
    return candidates[-1]


def _module_name(path: Path) -> str | None:
    parts = path.parts
    package_index = _package_index(parts)
    if package_index is None:
        return None

    module_parts = list(parts[package_index:])
    if module_parts[-1].endswith(".py"):
        module_parts[-1] = module_parts[-1][:-3]
    if module_parts[-1] == "__init__":
        module_parts.pop()
    return ".".join(module_parts) or PACKAGE


def _layer_for_module(module: str) -> str | None:
    parts = module.split(".")
    if not parts or parts[0] != PACKAGE:
        return None
    if len(parts) == 1:
        return "package"
    return parts[1] if parts[1] in LAYERS else None


def _package_name(module: str, path: Path) -> str:
    if path.name == "__init__.py":
        return module
    return module.rpartition(".")[0]


def _resolve_import_from(node: ast.ImportFrom, current_package: str) -> str | None:
    if node.level == 0:
        return node.module

    package_parts = current_package.split(".") if current_package else []
    parents_to_remove = node.level - 1
    if parents_to_remove > len(package_parts):
        return None
    if parents_to_remove:
        package_parts = package_parts[:-parents_to_remove]

    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _is_internal(module: str) -> bool:
    return module == PACKAGE or module.startswith(f"{PACKAGE}.")


def _is_allowed(target: str, allowed_prefixes: tuple[str, ...]) -> bool:
    return any(target == prefix or target.startswith(f"{prefix}.") for prefix in allowed_prefixes)


def _specific_rule(source_layer: str, target_layer: str | None) -> str:
    if source_layer == "service" and target_layer == "repo":
        return "ARCH-003"
    if source_layer == "ui" and target_layer == "repo":
        return "ARCH-004"
    return "ARCH-001"


def _collect_imports(
    tree: ast.Module, module: str, path: Path
) -> tuple[list[ImportReference], list[Violation]]:
    package = _package_name(module, path)
    imports: list[ImportReference] = []
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(ImportReference(alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_import_from(node, package)
            if target:
                imports.append(ImportReference(target, node.lineno))
                for alias in node.names:
                    if alias.name != "*":
                        imports.append(ImportReference(f"{target}.{alias.name}", node.lineno))
        elif isinstance(node, ast.Call):
            function_name: str | None = None
            if isinstance(node.func, ast.Name):
                function_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                function_name = node.func.attr
            if function_name in DYNAMIC_IMPORT_NAMES:
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        "ARCH-007",
                        "受控层禁止动态 import",
                    )
                )

    return imports, violations


def _cycle_violations(
    graph: dict[str, set[str]], module_paths: dict[str, Path]
) -> list[Violation]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(module: str) -> None:
        nonlocal index
        indices[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)

        for target in graph.get(module, set()):
            if target not in indices:
                visit(target)
                lowlinks[module] = min(lowlinks[module], lowlinks[target])
            elif target in on_stack:
                lowlinks[module] = min(lowlinks[module], indices[target])

        if lowlinks[module] == indices[module]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == module:
                    break
            components.append(component)

    for module in graph:
        if module not in indices:
            visit(module)

    violations: list[Violation] = []
    for component in components:
        component = [module for module in component if module in module_paths]
        has_self_loop = len(component) == 1 and component[0] in graph.get(component[0], set())
        if len(component) > 1 or has_self_loop:
            cycle = " → ".join(sorted(component) + [sorted(component)[0]])
            path = module_paths[sorted(component)[0]]
            violations.append(Violation(path, 1, "ARCH-005", f"检测到循环依赖：{cycle}"))
    return violations


def analyze_files(paths: Iterable[Path]) -> list[Violation]:
    violations: list[Violation] = []
    graph: dict[str, set[str]] = defaultdict(set)
    module_paths: dict[str, Path] = {}

    for path in sorted(set(paths)):
        if any(part in EXEMPT_PARTS for part in path.parts):
            continue
        module = _module_name(path)
        if module is None:
            continue
        source_layer = _layer_for_module(module)
        if source_layer is None:
            continue
        module_paths[module] = path

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            violations.append(
                Violation(
                    path,
                    getattr(exc, "lineno", 1) or 1,
                    "ARCH-000",
                    f"无法解析 Python 文件：{exc}",
                )
            )
            continue

        imports, dynamic_violations = _collect_imports(tree, module, path)
        violations.extend(dynamic_violations)
        allowed_prefixes = ALLOWED_INTERNAL_PREFIXES.get(source_layer, ())
        reported: set[tuple[int, str, str]] = set()

        for reference in imports:
            if not _is_internal(reference.module):
                continue
            target_layer = _layer_for_module(reference.module)
            if target_layer not in LAYERS:
                continue
            graph[module].add(reference.module)
            cross_layer_init = path.name == "__init__.py" and target_layer != source_layer
            if cross_layer_init or not _is_allowed(reference.module, allowed_prefixes):
                rule = (
                    "ARCH-006" if cross_layer_init else _specific_rule(source_layer, target_layer)
                )
                key = (reference.line, target_layer, rule)
                if key in reported:
                    continue
                reported.add(key)
                violations.append(
                    Violation(
                        path,
                        reference.line,
                        rule,
                        f"{module} 不允许依赖 {reference.module}",
                    )
                )

    violations.extend(_cycle_violations(graph, module_paths))
    return sorted(violations, key=lambda item: (str(item.path), item.line, item.rule))


def collect_python_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*.py"))
    return sorted(set(files))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("src/illumination")])
    parser.add_argument("--warn-only", action="store_true", help="报告违规但始终以成功状态退出")
    args = parser.parse_args(argv)

    violations = analyze_files(collect_python_files(args.paths))
    if not violations:
        print("arch_checker: OK")
        return 0

    for violation in violations:
        print(violation, file=sys.stderr)
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
