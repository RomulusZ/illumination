"""Deterministic test doubles for ports."""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from illumination.types.common import ArtifactRef
from illumination.types.llm import LLMRequest, LLMResponse, LLMUsage


class FakeLLM:
    """Scripted LLMPort implementation; never performs I/O."""

    def __init__(self, responses: Iterable[LLMResponse | str], *, model: str = "fake-llm") -> None:
        self._responses = deque(responses)
        self._model = model
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeLLM 没有剩余响应")
        response = self._responses.popleft()
        if isinstance(response, str):
            return LLMResponse(
                text=response,
                model=self._model,
                usage=LLMUsage(input_tokens=0, output_tokens=0),
            )
        return response


class FakeClock:
    """Manual clock for deterministic timestamps."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class InMemoryArtifactStore:
    """ArtifactStorePort implementation backed by a dict."""

    def __init__(self) -> None:
        self.items: dict[str, tuple[ArtifactRef, bytes]] = {}

    def put(self, *, name: str, content: bytes) -> ArtifactRef:
        sha256 = hashlib.sha256(content).hexdigest()
        artifact_id = f"art_{sha256[:16]}"
        ref = ArtifactRef(
            artifact_id=artifact_id,
            path=f"memory://{artifact_id}/{name}",
            sha256=sha256,
            summary=name,
        )
        self.items[artifact_id] = (ref, content)
        return ref

    def read(self, artifact_id: str) -> bytes:
        return self.items[artifact_id][1]
