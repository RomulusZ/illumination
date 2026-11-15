"""Ports implemented by infrastructure adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from illumination.types.common import ArtifactRef
from illumination.types.llm import LLMRequest, LLMResponse


@runtime_checkable
class LLMPort(Protocol):
    """External LLM capability used by services."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return one normalized completion for the request."""
        ...


@runtime_checkable
class ClockPort(Protocol):
    """Injectable time source; tests must not depend on the wall clock."""

    def now(self) -> datetime:
        """Return the current timezone-aware instant."""
        ...


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Durable artifact sink; graph state only carries pointers."""

    def put(self, *, name: str, content: bytes) -> ArtifactRef:
        """Persist content and return its pointer."""
        ...

    def read(self, artifact_id: str) -> bytes:
        """Load the payload for an artifact pointer."""
        ...
