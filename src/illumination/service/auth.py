"""Invite-code login and process-local opaque session authentication."""

import hmac
import secrets
import uuid

from illumination.service.errors import (
    AuthenticationError,
    ConfigurationError,
    CSRFValidationError,
    InvalidInviteCodeError,
)
from illumination.types.auth import AuthSession, LoginResult
from illumination.types.ports import SessionStorePort


class AuthService:
    """Authenticate invites and issue independent session and CSRF tokens."""

    def __init__(self, *, invite_codes: str, sessions: SessionStorePort) -> None:
        self._invites = _parse_invite_codes(invite_codes)
        self._sessions = sessions
        self._user_ids: dict[str, str] = {}

    def login(self, invite_code: str) -> LoginResult:
        invite = next(
            (
                (code, name)
                for code, name in self._invites
                if hmac.compare_digest(code, invite_code)
            ),
            None,
        )
        if invite is None:
            raise InvalidInviteCodeError("邀请码无效")

        code, display_name = invite
        user_id = self._user_ids.setdefault(code, str(uuid.uuid4()))
        access_token = secrets.token_urlsafe(32)
        session = AuthSession(
            user_id=user_id,
            display_name=display_name,
            csrf_token=secrets.token_urlsafe(32),
        )
        self._sessions.save(access_token, session)
        return LoginResult(session=session, access_token=access_token)

    def authenticate(self, access_token: str) -> AuthSession:
        session = self._sessions.get(access_token)
        if session is None:
            raise AuthenticationError("会话不存在或已失效")
        return session

    def logout(self, access_token: str) -> None:
        self._sessions.delete(access_token)

    @staticmethod
    def validate_csrf(session: AuthSession, header_token: str, cookie_token: str) -> None:
        valid = (
            bool(header_token)
            and bool(cookie_token)
            and hmac.compare_digest(header_token, cookie_token)
            and hmac.compare_digest(header_token, session.csrf_token)
        )
        if not valid:
            raise CSRFValidationError("CSRF 校验失败")


def _parse_invite_codes(value: str) -> tuple[tuple[str, str], ...]:
    invites: list[tuple[str, str]] = []
    for item in value.split(","):
        if not item.strip():
            continue
        code, separator, display_name = item.partition("=")
        if not separator or not code.strip() or not display_name.strip():
            raise ConfigurationError("ILLUMINATION_INVITE_CODES 必须使用 code=display_name 格式")
        invites.append((code.strip(), display_name.strip()))
    return tuple(invites)
