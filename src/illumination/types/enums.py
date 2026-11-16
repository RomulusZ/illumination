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


class GoalIntent(StrEnum):
    """目标的意图类型；maintain 不允许占用主位。"""

    ADVANCE = "advance"
    MAINTAIN = "maintain"


class GoalRole(StrEnum):
    """目标名额角色：1 个主位 + 最多 2 个辅位（P0 单目标切片）。"""

    PRIMARY = "primary"
    SECONDARY = "secondary"


class GoalChangeKind(StrEnum):
    """目标版本变更类型；版本只追加，不回写历史。"""

    INITIAL = "initial"
    SEMANTIC = "semantic"
    INTENT_CHANGE = "intent_change"
    REPLAN = "replan"


class MetricKey(StrEnum):
    """指标在目标版本内的固定槽位。"""

    PRIMARY = "primary"
    SECONDARY_1 = "secondary_1"
    SECONDARY_2 = "secondary_2"


class MetricKind(StrEnum):
    """指标形态，决定目标值的表达方式。"""

    NUMERIC = "numeric"
    STATE = "state"
    ARTIFACT = "artifact"


class MetricDirection(StrEnum):
    """数值指标的期望方向。"""

    UP = "up"
    DOWN = "down"
    HOLD = "hold"


class BaselineState(StrEnum):
    """基线三态；unknown 时不得给出数值。"""

    MEASURED = "measured"
    ESTIMATE = "estimate"
    UNKNOWN = "unknown"


class ThresholdSource(StrEnum):
    """阈值来源；不含模型生成。"""

    USER_INPUT = "user_input"
    TEMPLATE_DEFAULT = "template_default"
    USER_CONFIRMED_CANDIDATE = "user_confirmed_candidate"


class ThresholdWriteWindow(StrEnum):
    """阈值只允许在锁定前或重规划两个窗口写入。"""

    PLAN = "plan"
    REPLAN = "replan"


class IntervalSource(StrEnum):
    """反馈间隔的来源；推断值必须由用户确认后才可进 L3。"""

    USER_INPUT = "user_input"
    TEMPLATE_DEFAULT = "template_default"
    RULE_INFERRED = "rule_inferred"
    FALLBACK = "fallback"


class ActionType(StrEnum):
    """动作是一次性还是周期重复。"""

    ONCE = "once"
    RECURRING = "recurring"


class ActionStatus(StrEnum):
    """动作状态；done / failed 必须有完成时间。"""

    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class EvidenceMode(StrEnum):
    """证据模式；log_only 只记录，不进入单元判定。"""

    NORMAL = "normal"
    LOG_ONLY = "log_only"


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
