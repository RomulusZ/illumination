"""本地业务日期、周期窗口与客户端时钟偏差规则。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from illumination.service.errors import ClockSkewRejected
from illumination.service.time_rules import business_date, cycle_window, resolve_client_time

TZ = "Asia/Shanghai"
CUTOFF = time(4, 0)


def test_just_before_cutoff_belongs_to_previous_business_day() -> None:
    # 上海 2026-11-16 03:59
    instant = datetime(2026, 11, 15, 19, 59, tzinfo=UTC)

    assert business_date(instant, timezone=TZ, day_cutoff=CUTOFF) == date(2026, 11, 15)


def test_exact_cutoff_starts_new_business_day() -> None:
    # 上海 2026-11-16 04:00
    instant = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)

    assert business_date(instant, timezone=TZ, day_cutoff=CUTOFF) == date(2026, 11, 16)


def test_business_date_crosses_month_boundary() -> None:
    # 上海 2027-01-01 03:00
    instant = datetime(2026, 12, 31, 19, 0, tzinfo=UTC)

    assert business_date(instant, timezone=TZ, day_cutoff=CUTOFF) == date(2026, 12, 31)


def test_business_date_requires_timezone_aware_input() -> None:
    with pytest.raises(ValueError, match="时区"):
        business_date(datetime(2026, 11, 16, 4, 0), timezone=TZ, day_cutoff=CUTOFF)


def test_cycle_window_is_left_closed_right_open() -> None:
    start, end = cycle_window(date(2026, 11, 16), days=7, timezone=TZ, day_cutoff=CUTOFF)
    zone = ZoneInfo(TZ)

    assert start == datetime(2026, 11, 16, 4, 0, tzinfo=zone)
    assert end == datetime(2026, 11, 23, 4, 0, tzinfo=zone)
    assert business_date(start, timezone=TZ, day_cutoff=CUTOFF) == date(2026, 11, 16)
    assert business_date(end, timezone=TZ, day_cutoff=CUTOFF) == date(2026, 11, 23)


def test_cycle_window_crosses_year_boundary() -> None:
    start, end = cycle_window(date(2026, 12, 28), days=7, timezone=TZ, day_cutoff=CUTOFF)

    assert start == datetime(2026, 12, 28, 4, 0, tzinfo=ZoneInfo(TZ))
    assert end == datetime(2027, 1, 4, 4, 0, tzinfo=ZoneInfo(TZ))


def test_cycle_window_rejects_non_positive_days() -> None:
    with pytest.raises(ValueError, match="days"):
        cycle_window(date(2026, 11, 16), days=0, timezone=TZ, day_cutoff=CUTOFF)


def test_business_date_is_dst_aware() -> None:
    zone = "America/New_York"
    # 2026-03-08 美东进入夏令时：01:59 EST 仍属前一日，04:00 EDT 属于当日。
    before = datetime(2026, 3, 8, 6, 59, tzinfo=UTC)
    after = datetime(2026, 3, 8, 8, 0, tzinfo=UTC)

    assert business_date(before, timezone=zone, day_cutoff=CUTOFF) == date(2026, 3, 7)
    assert business_date(after, timezone=zone, day_cutoff=CUTOFF) == date(2026, 3, 8)


def test_cycle_window_keeps_local_cutoff_across_dst() -> None:
    start, end = cycle_window(
        date(2026, 3, 7), days=1, timezone="America/New_York", day_cutoff=CUTOFF
    )

    assert start.hour == end.hour == 4
    assert start.utcoffset() != end.utcoffset()


def test_client_time_within_limit_is_kept() -> None:
    server = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    client = server + timedelta(minutes=5)

    result = resolve_client_time(client, server, strategy="clamp_and_warn", limit_minutes=10)

    assert result.resolved_at == client
    assert result.clamped is False
    assert result.reason is None
    assert result.skew_minutes == 5


def test_client_time_ahead_is_clamped_to_server_time() -> None:
    server = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    client = server + timedelta(minutes=30)

    result = resolve_client_time(client, server, strategy="clamp_and_warn", limit_minutes=10)

    assert result.resolved_at == server
    assert result.clamped is True
    assert result.reason == "client_ahead"
    assert result.skew_minutes == 30


def test_client_time_behind_is_clamped_to_server_time() -> None:
    server = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    client = server - timedelta(minutes=30)

    result = resolve_client_time(client, server, strategy="clamp_and_warn", limit_minutes=10)

    assert result.resolved_at == server
    assert result.clamped is True
    assert result.reason == "client_behind"


def test_reject_strategy_raises_stable_error() -> None:
    server = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    client = server + timedelta(minutes=30)

    with pytest.raises(ClockSkewRejected) as error:
        resolve_client_time(client, server, strategy="reject", limit_minutes=10)

    assert error.value.code == "clock_skew_rejected"
