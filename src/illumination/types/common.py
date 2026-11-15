"""Cross-cutting run enums and artifact references."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from illumination.types.base import IlluminationModel

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RunStatus(StrEnum):
    """Terminal or in-flight state of one harness run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class StepStatus(StrEnum):
    """State of one plan step."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


class ArtifactRef(IlluminationModel):
    """Pointer to a durable artifact; the payload stays outside the graph state."""

    artifact_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    summary: str = Field(min_length=1)
