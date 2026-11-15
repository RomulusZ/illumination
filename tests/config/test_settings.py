"""Settings tests; never read the developer's .env file."""

import pytest
from pydantic import ValidationError

from illumination.config.settings import Settings


def test_defaults_are_dev_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.illumination_env == "dev"
    assert not settings.is_prod
    assert settings.illumination_max_steps == 8


def test_prod_requires_openai_api_key() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, illumination_env="prod", openai_api_key="")


def test_prod_with_key_passes() -> None:
    settings = Settings(
        _env_file=None,
        illumination_env="prod",
        openai_api_key="sk-test",
    )
    assert settings.is_prod
    assert "sk-test" not in repr(settings)


def test_invalid_temperature_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, illumination_model_temperature=3.0)


def test_tracing_requires_api_key() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key=None)
