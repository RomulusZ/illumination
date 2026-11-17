"""Process-local session store for the P0 HTTP application."""

from illumination.types.auth import AuthSession


class InMemorySessionStore:
    """Opaque-token session storage; restart or logout invalidates sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, AuthSession] = {}

    def save(self, token: str, session: AuthSession) -> None:
        self._sessions[token] = session

    def get(self, token: str) -> AuthSession | None:
        return self._sessions.get(token)

    def delete(self, token: str) -> None:
        self._sessions.pop(token, None)
