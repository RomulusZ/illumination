"""Pytest 全局配置与共享 fixture。"""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """返回仓库根目录，供路径相关测试使用。"""
    return Path(__file__).resolve().parents[1]
