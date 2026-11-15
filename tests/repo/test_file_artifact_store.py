"""Filesystem artifact store tests."""

from pathlib import Path

import pytest

from illumination.repo.errors import ArtifactStoreError
from illumination.repo.file_artifact_store import FileArtifactStore


def test_put_and_read_roundtrip(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(name="note.txt", content=b"hello")
    assert store.read(ref.artifact_id) == b"hello"
    assert len(ref.sha256) == 64


def test_artifact_id_is_content_addressed(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    first = store.put(name="a.txt", content=b"same")
    second = store.put(name="b.txt", content=b"same")
    assert first.artifact_id == second.artifact_id


def test_put_rejects_path_traversal(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    with pytest.raises(ArtifactStoreError):
        store.put(name="../escape.txt", content=b"x")


def test_read_rejects_invalid_artifact_id(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    with pytest.raises(ArtifactStoreError):
        store.read("../../etc/passwd")
