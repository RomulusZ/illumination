"""冒烟测试：确认包可导入、骨架完整。"""

import importlib
from pathlib import Path

import illumination

LAYERS = ("types", "config", "repo", "service", "runtime", "ui")


def test_package_version() -> None:
    assert illumination.__version__ == "0.1.0"


def test_subpackages_importable() -> None:
    for layer in LAYERS:
        module = importlib.import_module(f"illumination.{layer}")
        assert module is not None


def test_package_layout(repo_root: Path) -> None:
    package_dir = repo_root / "src" / "illumination"
    assert package_dir.is_dir()
    assert (package_dir / "py.typed").is_file()
    for layer in LAYERS:
        assert (package_dir / layer / "__init__.py").is_file()


def test_package_is_importable_from_src(repo_root: Path) -> None:
    package_file = Path(illumination.__file__).resolve()
    expected = (repo_root / "src" / "illumination" / "__init__.py").resolve()
    assert package_file == expected
