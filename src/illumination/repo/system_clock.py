"""Timezone-aware system clock adapter."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """ClockPort implementation backed by the wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)
