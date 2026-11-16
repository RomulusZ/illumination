"""Domain enumerations shared by contracts and services.

取值与 `db/schema_v0.1.sql` 的 PostgreSQL 枚举保持一致；状态机与判定规则属于
Service 层，这里只固定词汇表，避免各层各自定义字符串常量。
"""

from __future__ import annotations

from enum import StrEnum


class GoalStatus(StrEnum):
    """目标生命周期状态（8 态）。"""

    PLANNED = "planned"
    ACTIVE = "active"
    ACHIEVED = "achieved"
    ARCHIVED = "archived"
    ABANDONED = "abandoned"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    PAUSED = "paused"


class CycleState(StrEnum):
    """节奏单元状态（4 态）。"""

    FROZEN = "frozen"
    ACTIVE = "active"
    CLOSING = "closing"
    REVIEWED = "reviewed"


class EvidenceChannel(StrEnum):
    """证据的输入通道；与定值状态正交。"""

    MANUAL = "manual"
    DEVICE = "device"


class EvidenceStatus(StrEnum):
    """证据的定值状态；candidate 不计入任何计数与判定。"""

    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"


class ReadinessLevel(StrEnum):
    """就绪度 L0–L3；界面只显示对应的人话，不暴露级别。"""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class CycleResult(StrEnum):
    """周期评估结论；partial 是目标级属性，不是周期结论。"""

    MET = "met"
    NOT_MET = "not_met"
    INSUFFICIENT_DATA = "insufficient_data"
    ABORTED = "aborted"


class ReviewScope(StrEnum):
    """评估作用域。"""

    CYCLE = "cycle"
    STAGE = "stage"


class StageDecision(StrEnum):
    """阶段决策；advance 目标用前五个，maintain 目标用后三个。"""

    CONTINUE = "continue"
    ADJUST_PLAN = "adjust_plan"
    RENEGOTIATE = "renegotiate"
    REPLAN = "replan"
    CLOSE = "close"
    CONTINUE_MAINTAIN = "continue_maintain"
    ADJUST_STANDARD = "adjust_standard"
    STOP_MAINTAIN = "stop_maintain"
