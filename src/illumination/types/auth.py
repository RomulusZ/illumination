"""Authentication contracts shared by the service and HTTP boundary."""

from typing import Literal

from pydantic import Field, SecretStr

from illumination.types.base import IlluminationModel


class AuthSession(IlluminationModel):
    """Server-side session identity; user_id never comes from a request body."""

    user_id: str
    display_name: str
    csrf_token: str


class LoginResult(IlluminationModel):
    session: AuthSession
    access_token: str


class LoginRequest(IlluminationModel):
    invite_code: SecretStr = Field(min_length=1, max_length=256)


class LoginResponse(IlluminationModel):
    user_id: str
    display_name: str
    access_token: str
    csrf_token: str
    token_type: Literal["bearer"] = "bearer"


class CurrentUserResponse(IlluminationModel):
    user_id: str
    display_name: str


class LogoutResponse(IlluminationModel):
    status: Literal["logged_out"] = "logged_out"


class HealthResponse(IlluminationModel):
    status: Literal["ok"] = "ok"
