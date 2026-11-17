"""Invite login, session dependencies, and cookie CSRF protection."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from illumination.runtime.composition import Runtime
from illumination.service.auth import AuthService
from illumination.service.errors import AuthenticationError, ConfigurationError
from illumination.types.auth import (
    AuthSession,
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
)

_SESSION_COOKIE = "illumination_session"
_CSRF_COOKIE = "illumination_csrf"

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def get_auth_service(request: Request) -> AuthService:
    runtime: Runtime = request.app.state.runtime
    if runtime.auth is None:
        raise ConfigurationError("认证服务未装配")
    return runtime.auth


AuthDependency = Annotated[AuthService, Depends(get_auth_service)]


def get_current_session(request: Request, auth: AuthDependency) -> AuthSession:
    authorization = request.headers.get("Authorization", "")
    scheme, _, bearer_token = authorization.partition(" ")
    via_cookie = not authorization
    if authorization and (scheme.lower() != "bearer" or not bearer_token):
        raise AuthenticationError("Authorization 必须使用 Bearer 会话令牌")

    token = request.cookies.get(_SESSION_COOKIE, "") if via_cookie else bearer_token
    if not token:
        raise AuthenticationError("缺少会话凭证")
    request.state.session_token = token
    request.state.via_cookie = via_cookie
    return auth.authenticate(token)


SessionDependency = Annotated[AuthSession, Depends(get_current_session)]


def require_csrf(
    request: Request, session: SessionDependency, auth: AuthDependency
) -> AuthSession:
    if request.state.via_cookie:
        auth.validate_csrf(
            session,
            request.headers.get("X-CSRF-Token", ""),
            request.cookies.get(_CSRF_COOKIE, ""),
        )
    return session


CSRFSessionDependency = Annotated[AuthSession, Depends(require_csrf)]


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest, request: Request, response: Response, auth: AuthDependency
) -> LoginResponse:
    result = auth.login(payload.invite_code.get_secret_value())
    secure = request.app.state.settings.is_prod
    response.set_cookie(
        _SESSION_COOKIE,
        result.access_token,
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        _CSRF_COOKIE,
        result.session.csrf_token,
        httponly=False,
        secure=secure,
        samesite="strict",
    )
    return LoginResponse(
        user_id=result.session.user_id,
        display_name=result.session.display_name,
        access_token=result.access_token,
        csrf_token=result.session.csrf_token,
    )


@router.get("/me", response_model=CurrentUserResponse)
def current_user(session: SessionDependency) -> CurrentUserResponse:
    return CurrentUserResponse(user_id=session.user_id, display_name=session.display_name)


@router.post("/logout", response_model=LogoutResponse)
def logout(
    request: Request,
    response: Response,
    session: CSRFSessionDependency,
    auth: AuthDependency,
) -> LogoutResponse:
    auth.logout(request.state.session_token)
    response.delete_cookie(_SESSION_COOKIE)
    response.delete_cookie(_CSRF_COOKIE)
    return LogoutResponse()
