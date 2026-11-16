"""Settings tests; never read the developer's .env file."""

import pytest
from pydantic import ValidationError

from illumination.config.settings import Settings


def test_defaults_are_dev_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.illumination_env == "dev"
    assert not settings.is_prod
    assert settings.illumination_max_steps == 8
    assert (
        settings.illumination_database_url
        == "postgresql+psycopg://postgres@127.0.0.1:15432/illumination"
    )
    assert (
        settings.illumination_test_database_url
        == "postgresql+psycopg://postgres@127.0.0.1:15432/illumination_test"
    )
    assert settings.illumination_agent_mode == "off"
    assert settings.illumination_day_cutoff == "04:00"
    assert settings.illumination_latest_start_time == "21:00"
    assert settings.illumination_clock_skew_strategy == "clamp_and_warn"
    assert settings.illumination_clock_skew_limit_minutes == 10


def test_prod_requires_openai_api_key() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            illumination_env="prod",
            openai_api_key="",
            illumination_database_url="postgresql+psycopg://db.internal/illumination",
            illumination_invite_codes="bb_test=测试用户",
        )


def test_prod_with_required_settings_passes() -> None:
    settings = Settings(
        _env_file=None,
        illumination_env="prod",
        openai_api_key="sk-test",
        illumination_database_url="postgresql+psycopg://localhost/illumination",
        illumination_invite_codes="bb_test=测试用户",
    )
    assert settings.is_prod
    assert "sk-test" not in repr(settings)
    assert "bb_test" not in repr(settings)


def test_prod_requires_invite_codes() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            illumination_env="prod",
            openai_api_key="sk-test",
            illumination_database_url="postgresql+psycopg://db.internal/illumination",
            illumination_invite_codes="",
        )
    assert "INVITE_CODES" in str(exc_info.value)


def test_prod_requires_explicit_database_url() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            illumination_env="prod",
            openai_api_key="sk-test",
            illumination_invite_codes="bb_test=测试用户",
        )
    assert "DATABASE_URL" in str(exc_info.value)


@pytest.mark.parametrize(
    "database_url",
    ["", "not-a-url", "sqlite:///./.data/illumination.db", "mysql://localhost/illumination"],
)
def test_invalid_or_non_postgresql_database_url_is_rejected(database_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, illumination_database_url=database_url)


@pytest.mark.parametrize("field", ["illumination_database_url", "illumination_test_database_url"])
def test_both_database_urls_reject_non_postgresql(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: "sqlite:///./.data/illumination.db"})


def test_test_database_must_differ_from_development_database() -> None:
    shared = "postgresql+psycopg://postgres@127.0.0.1:15432/illumination"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            illumination_database_url=shared,
            illumination_test_database_url=shared,
        )
    assert "测试数据库" in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("illumination_day_cutoff", "24:00"),
        ("illumination_day_cutoff", "4:00"),
        ("illumination_latest_start_time", "21:60"),
    ],
)
def test_invalid_time_boundary_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_invalid_agent_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, illumination_agent_mode="automatic")


def test_invalid_temperature_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, illumination_model_temperature=3.0)


def test_tracing_requires_api_key() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key=None)
