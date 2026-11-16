"""显式时间的业务日期与周期归属规则。

所有函数只使用调用方传入的时间与时区，不读取系统时钟；同一输入始终得到同一
业务日期。日界按用户本地时区的 `day_cutoff` 划分，周期窗口左闭右开。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from illumination.service.errors import ClockSkewRejected

SkewStrategy = Literal["clamp_and_warn", "reject"]


@dataclass(frozen=True, slots=True)
class SkewResult:
    """客户端时间裁决结果；clamped 时 resolved_at 已被服务器时间替代。"""

    resolved_at: datetime
    skew_minutes: float
    clamped: bool
    reason: Literal["client_ahead", "client_behind"] | None


def _require_aware(moment: datetime, name: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} 必须是带时区的时间")
    return moment


def business_date(instant: datetime, *, timezone: str, day_cutoff: time) -> date:
    """把瞬间归属到本地业务日；早于日界算前一天。"""
    _require_aware(instant, "instant")
    local = instant.astimezone(ZoneInfo(timezone))
    if local.time() < day_cutoff:
        return local.date() - timedelta(days=1)
    return local.date()


def cycle_window(
    anchor: date, *, days: int, timezone: str, day_cutoff: time
) -> tuple[datetime, datetime]:
    """从锚定业务日生成左闭右开的单元窗口，边界取本地日界。"""
    if days < 1:
        raise ValueError("days 必须大于等于 1")
    zone = ZoneInfo(timezone)
    start = datetime.combine(anchor, day_cutoff, tzinfo=zone)
    end = datetime.combine(anchor + timedelta(days=days), day_cutoff, tzinfo=zone)
    return start, end


def resolve_client_time(
    client_at: datetime,
    server_at: datetime,
    *,
    strategy: SkewStrategy,
    limit_minutes: int,
) -> SkewResult:
    """按配置策略裁决客户端时间；超限时钳制到服务器时间或直接拒绝。"""
    _require_aware(client_at, "client_at")
    _require_aware(server_at, "server_at")
    if limit_minutes <= 0:
        raise ValueError("limit_minutes 必须大于 0")

    skew_minutes = (client_at - server_at).total_seconds() / 60
    if abs(skew_minutes) <= limit_minutes:
        return SkewResult(
            resolved_at=client_at, skew_minutes=skew_minutes, clamped=False, reason=None
        )
    if strategy == "reject":
        raise ClockSkewRejected(
            f"客户端时钟偏差 {skew_minutes:.1f} 分钟超过上限 {limit_minutes} 分钟"
        )
    return SkewResult(
        resolved_at=server_at,
        skew_minutes=skew_minutes,
        clamped=True,
        reason="client_ahead" if skew_minutes > 0 else "client_behind",
    )
