"""Fixture tests for the architecture import checker."""

from __future__ import annotations

from pathlib import Path

from lint.arch_checker import Violation, analyze_files, collect_python_files


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _analyze(root: Path) -> list[Violation]:
    return analyze_files(collect_python_files([root]))


def test_allowed_layer_dependency_passes(tmp_path: Path) -> None:
    _write(tmp_path, "illumination/repo/llm_adapter.py", "from illumination.types import llm\n")
    assert _analyze(tmp_path) == []


def test_service_importing_repo_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "illumination/service/planning.py",
        "from illumination.repo import llm_adapter\n",
    )
    violations = _analyze(tmp_path)
    assert [violation.rule for violation in violations] == ["ARCH-003"]


def test_ui_importing_repo_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "illumination/ui/cli.py",
        "from illumination.repo.system_clock import SystemClock\n",
    )
    violations = _analyze(tmp_path)
    assert [violation.rule for violation in violations] == ["ARCH-004"]


def test_relative_import_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "illumination/service/planning.py", "from ..repo import llm_adapter\n")
    violations = _analyze(tmp_path)
    assert [violation.rule for violation in violations] == ["ARCH-003"]


def test_from_package_import_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "illumination/repo/cache.py", "from illumination import service\n")
    violations = _analyze(tmp_path)
    assert [violation.rule for violation in violations] == ["ARCH-001"]


def test_top_level_init_cross_layer_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "illumination/__init__.py", "from illumination.types import llm\n")
    violations = _analyze(tmp_path)
    assert [violation.rule for violation in violations] == ["ARCH-006"]


def test_subpackage_init_cross_layer_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "illumination/service/__init__.py", "from illumination.types import llm\n")
    violations = _analyze(tmp_path)
    assert [violation.rule for violation in violations] == ["ARCH-006"]


def test_cycle_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "illumination/service/a.py", "from illumination.service import b\n")
    _write(tmp_path, "illumination/service/b.py", "from illumination.service import a\n")
    violations = _analyze(tmp_path)
    assert any(violation.rule == "ARCH-005" for violation in violations)


def test_dynamic_import_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "illumination/service/loader.py",
        "import importlib\nimportlib.import_module('illumination.repo')\n",
    )
    violations = _analyze(tmp_path)
    assert any(violation.rule == "ARCH-007" for violation in violations)


def test_src_layout_wins_over_repo_folder_name(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "illumination/src/illumination/service/planning.py",
        "from illumination.repo import llm_adapter\n",
    )
    violations = _analyze(tmp_path)
    assert [violation.rule for violation in violations] == ["ARCH-003"]
