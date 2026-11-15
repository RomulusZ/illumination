"""Filesystem artifact store adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path

from illumination.repo.errors import ArtifactStoreError
from illumination.types.common import ArtifactRef


class FileArtifactStore:
    """ArtifactStorePort implementation rooted at one workspace directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, *, name: str, content: bytes) -> ArtifactRef:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ArtifactStoreError("artifact 名称必须是纯文件名")

        sha256 = hashlib.sha256(content).hexdigest()
        artifact_id = f"art_{sha256[:16]}"
        target = self._root / f"{artifact_id}-{name}"
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as exc:
            raise ArtifactStoreError(f"无法写入 artifact：{name}") from exc

        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(target),
            sha256=sha256,
            summary=name,
        )

    def read(self, artifact_id: str) -> bytes:
        if not artifact_id.startswith("art_") or any(char in artifact_id for char in "\\/."):
            raise ArtifactStoreError("artifact_id 非法")

        matches = sorted(self._root.glob(f"{artifact_id}-*"))
        if len(matches) != 1:
            raise ArtifactStoreError(f"artifact 不存在或不唯一：{artifact_id}")
        try:
            return matches[0].read_bytes()
        except OSError as exc:
            raise ArtifactStoreError(f"无法读取 artifact：{artifact_id}") from exc
