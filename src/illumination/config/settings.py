"""ILLUMINATION_* 与外部服务环境变量的唯一读取入口。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

Env = Literal["dev", "test", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AgentMode = Literal["off", "explore", "all"]
ClockSkewStrategy = Literal["clamp_and_warn", "reject"]

_DEV_DATABASE_URL = "postgresql+psycopg://localhost:5432/illumination"


class Settings(BaseSettings):
    """应用配置。必填项缺失或取值非法时必须启动即失败。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 1. 运行身份
    illumination_env: Env = "dev"
    illumination_run_id_prefix: str = "ill"
    illumination_log_level: LogLevel = "INFO"
    illumination_timezone: str = "Asia/Shanghai"

    # 2. 应用与持久化
    illumination_database_url: str = _DEV_DATABASE_URL
    illumination_invite_codes: SecretStr = SecretStr("")
    illumination_agent_mode: AgentMode = "off"
    illumination_day_cutoff: str = Field(default="04:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    illumination_latest_start_time: str = Field(
        default="21:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    illumination_clock_skew_strategy: ClockSkewStrategy = "clamp_and_warn"
    illumination_clock_skew_limit_minutes: int = Field(default=10, gt=0, le=60)

    # 3. 模型
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_api_key: SecretStr = SecretStr("")
    illumination_model_main: str = "deepseek-chat"
    illumination_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    illumination_model_max_tokens: int = Field(default=4096, gt=0)
    illumination_http_timeout: float = Field(default=30.0, gt=0)
    illumination_http_max_attempts: int = Field(default=3, gt=0, le=5)
    illumination_retry_wait_seconds: float = Field(default=0.5, ge=0.0)

    # 4. 记忆与产物
    illumination_workspace_root: Path = Path("./.data/runs")
    illumination_audit_log_path: Path = Path("./.data/audit/audit.jsonl")
    illumination_retention_days: int = Field(default=30, gt=0)

    # 5. 预算
    illumination_max_steps: int = Field(default=8, gt=0, le=32)
    illumination_run_token_budget: int = Field(default=96000, gt=0)
    illumination_call_token_budget: int = Field(default=32000, gt=0)

    # 6. 追踪
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "illumination-dev"

    @field_validator("illumination_database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        try:
            url = make_url(value)
        except ArgumentError as exc:
            raise ValueError("ILLUMINATION_DATABASE_URL 必须是有效的 SQLAlchemy URL") from exc
        if url.get_backend_name() != "postgresql":
            raise ValueError("ILLUMINATION_DATABASE_URL 仅支持 PostgreSQL")
        return value

    @model_validator(mode="after")
    def _validate_conditionals(self) -> Self:
        if self.is_prod and not self.openai_api_key.get_secret_value().strip():
            raise ValueError("生产环境必须提供 OPENAI_API_KEY")
        if self.is_prod and self.illumination_database_url == _DEV_DATABASE_URL:
            raise ValueError("生产环境必须显式提供 ILLUMINATION_DATABASE_URL")
        if self.is_prod and not self.illumination_invite_codes.get_secret_value().strip():
            raise ValueError("生产环境必须提供 ILLUMINATION_INVITE_CODES")
        if self.langsmith_tracing and not self._secret_value(self.langsmith_api_key):
            raise ValueError("LANGSMITH_TRACING=true 时必须提供 LANGSMITH_API_KEY")
        return self

    @staticmethod
    def _secret_value(secret: SecretStr | None) -> str:
        return secret.get_secret_value().strip() if secret is not None else ""

    @property
    def is_prod(self) -> bool:
        return self.illumination_env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例；测试可用 Settings(_env_file=None, ...) 直接构造。"""
    return Settings()
