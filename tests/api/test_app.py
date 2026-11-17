"""HTTP application, authentication, and safe error mapping tests."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from illumination.config.settings import Settings
from illumination.service.errors import GoalNotReadyError
from illumination.ui.api.app import create_app


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,
        illumination_invite_codes="bb_test=测试用户,bb_other=另一位用户",
        openai_api_key="sk-test-secret",
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, stub=True)) as test_client:
        yield test_client


def _login(client: TestClient, invite_code: str = "bb_test") -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"invite_code": invite_code})
    assert response.status_code == 200
    return response.json()


def test_health_check_starts_complete_application(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_protected_endpoint_without_credentials_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_invite_login_supports_cookie_and_bearer_sessions(settings: Settings) -> None:
    with TestClient(create_app(settings, stub=True)) as cookie_client:
        payload = _login(cookie_client)

        assert payload["display_name"] == "测试用户"
        assert payload["token_type"] == "bearer"
        assert payload["access_token"]
        assert payload["csrf_token"]
        assert cookie_client.cookies.get("illumination_session") == payload["access_token"]
        assert cookie_client.cookies.get("illumination_csrf") == payload["csrf_token"]

        cookie_me = cookie_client.get("/api/v1/auth/me")
        assert cookie_me.status_code == 200
        assert cookie_me.json() == {
            "user_id": payload["user_id"],
            "display_name": "测试用户",
        }

    with TestClient(create_app(settings, stub=True)) as bearer_client:
        bearer_payload = _login(bearer_client)
        bearer_client.cookies.clear()
        bearer_me = bearer_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {bearer_payload['access_token']}"},
        )
        assert bearer_me.status_code == 200
        assert bearer_me.json()["user_id"] == bearer_payload["user_id"]


def test_login_rejects_client_supplied_user_id(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"invite_code": "bb_test", "user_id": "00000000-0000-0000-0000-000000000001"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_invalid_invite_code_is_rejected_without_echoing_secret(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"invite_code": "secret-invalid-code"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_invite_code"
    assert "secret-invalid-code" not in response.text


def test_cookie_logout_requires_csrf_and_revokes_session(client: TestClient) -> None:
    payload = _login(client)

    missing_csrf = client.post("/api/v1/auth/logout")
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_validation_failed"

    response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": payload["csrf_token"]})
    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}
    assert client.get("/api/v1/auth/me").status_code == 401


def test_bearer_logout_does_not_require_csrf(client: TestClient) -> None:
    payload = _login(client)
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {payload['access_token']}"}

    response = client.post("/api/v1/auth/logout", headers=headers)

    assert response.status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_domain_error_uses_stable_safe_response(settings: Settings) -> None:
    app = create_app(settings, stub=True)

    @app.get("/_test/domain-error")
    def domain_error() -> None:
        raise GoalNotReadyError("内部详情 sk-test-secret")

    with TestClient(app) as client:
        response = client.get("/_test/domain-error")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "goal_not_ready"
    assert "sk-test-secret" not in response.text
    assert "内部详情" not in response.text
    assert "traceback" not in response.text.lower()


def test_unknown_error_is_sanitized(settings: Settings) -> None:
    app = create_app(settings, stub=True)

    @app.get("/_test/internal-error")
    def internal_error() -> None:
        raise RuntimeError("sk-test-secret traceback internal detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/_test/internal-error")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "sk-test-secret" not in response.text
    assert "traceback" not in response.text.lower()
    assert "internal detail" not in response.text
