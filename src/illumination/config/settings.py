"""ILLUMINATION_* 与外部服务环境变量的唯一读取入口。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "test", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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

    # 2. 模型
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_api_key: SecretStr = SecretStr("")
    illumination_model_main: str = "deepseek-chat"
    illumination_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    illumination_model_max_tokens: int = Field(default=4096, gt=0)
    illumination_http_timeout: float = Field(default=30.0, gt=0)
    illumination_http_max_attempts: int = Field(default=3, gt=0, le=5)
    illumination_retry_wait_seconds: float = Field(default=0.5, ge=0.0)

    # 3. 记忆与产物
    illumination_workspace_root: Path = Path("./.data/runs")
    illumination_audit_log_path: Path = Path("./.data/audit/audit.jsonl")
    illumination_retention_days: int = Field(default=30, gt=0)

    # 4. 预算
    illumination_max_steps: int = Field(default=8, gt=0, le=32)
    illumination_run_token_budget: int = Field(default=96000, gt=0)
    illumination_call_token_budget: int = Field(default=32000, gt=0)

    # 5. 追踪
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "illumination-dev"

    @model_validator(mode="after")
    def _validate_conditionals(self) -> Self:
        if self.is_prod and not self.openai_api_key.get_secret_value().strip():
            raise ValueError("生产环境必须提供 OPENAI_API_KEY")
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
