"""Cross-cutting run enums and artifact references."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

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


class LocalDateRange(IlluminationModel):
    """用户本地业务日期的闭区间；首尾都计入。"""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date 不能早于 start_date")
        return self

    def contains(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date

    @property
    def days(self) -> int:
        """含首尾的天数。"""
        return (self.end_date - self.start_date).days + 1

    @classmethod
    def single_day(cls, day: date) -> Self:
        return cls(start_date=day, end_date=day)
