"""Composition root: the only place concrete adapters are instantiated."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from illumination.config.settings import Settings
from illumination.repo.errors import RepoError
from illumination.repo.file_artifact_store import FileArtifactStore
from illumination.repo.llm_adapter import OpenAICompatibleLLM
from illumination.repo.session_store import InMemorySessionStore
from illumination.repo.stub_llm import StubPlannerLLM
from illumination.repo.system_clock import SystemClock
from illumination.runtime.intake_graph import (
    IntakeGraphRequest,
    IntakeGraphResult,
    resume_intake_graph,
    run_intake_graph,
)
from illumination.runtime.pipeline import run_once
from illumination.service.auth import AuthService
from illumination.service.errors import ConfigurationError
from illumination.types.plan import RunRequest, RunResult
from illumination.types.ports import ArtifactStorePort, ClockPort, LLMPort, UnitOfWorkPort


@dataclass(frozen=True, slots=True)
class Runtime:
    """Wired runtime handed to the UI layer."""

    llm: LLMPort
    clock: ClockPort
    store: ArtifactStorePort
    run_id_prefix: str = "ill"
    auth: AuthService | None = None
    intake_checkpointer: Any = field(default_factory=InMemorySaver)

    async def run(self, request: RunRequest) -> RunResult:
        return await run_once(
            request=request,
            llm=self.llm,
            clock=self.clock,
            store=self.store,
            run_id_prefix=self.run_id_prefix,
        )

    async def run_intake(
        self, request: IntakeGraphRequest, *, uow: UnitOfWorkPort, thread_id: str
    ) -> IntakeGraphResult:
        return await run_intake_graph(
            request=request,
            llm=self.llm,
            clock=self.clock,
            store=self.store,
            uow=uow,
            checkpointer=self.intake_checkpointer,
            thread_id=thread_id,
        )

    async def resume_intake(self, *, uow: UnitOfWorkPort, thread_id: str) -> IntakeGraphResult:
        return await resume_intake_graph(
            llm=self.llm,
            clock=self.clock,
            store=self.store,
            uow=uow,
            checkpointer=self.intake_checkpointer,
            thread_id=thread_id,
        )


def build_runtime(
    settings: Settings,
    *,
    stub: bool = False,
    workspace_root: Path | None = None,
) -> Runtime:
    """Instantiate adapters and inject them into the runtime."""
    store = FileArtifactStore(workspace_root or settings.illumination_workspace_root)
    clock = SystemClock()

    llm: LLMPort
    if stub:
        llm = StubPlannerLLM()
    else:
        try:
            llm = OpenAICompatibleLLM(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.illumination_model_main,
                timeout=settings.illumination_http_timeout,
                max_attempts=settings.illumination_http_max_attempts,
                retry_wait_seconds=settings.illumination_retry_wait_seconds,
            )
        except RepoError as exc:
            raise ConfigurationError(f"无法初始化模型适配器：{exc}") from exc

    return Runtime(
        llm=llm,
        clock=clock,
        store=store,
        run_id_prefix=settings.illumination_run_id_prefix,
        auth=AuthService(
            invite_codes=settings.illumination_invite_codes.get_secret_value(),
            sessions=InMemorySessionStore(),
        ),
    )
