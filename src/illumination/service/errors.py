"""Service-level errors with stable codes."""

from __future__ import annotations


class IlluminationError(Exception):
    """Base for expected application errors."""

    code = "illumination_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StructuredOutputError(IlluminationError):
    """The model output is not valid JSON or violates the response contract."""

    code = "structured_output_invalid"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class PlanningError(IlluminationError):
    """A plan violates the request budget or contract."""

    code = "planning_invalid"


class ConfigurationError(IlluminationError):
    """Runtime dependencies cannot be assembled from the current settings."""

    code = "configuration_invalid"


class ClockSkewRejected(IlluminationError):
    """客户端时间与服务器时间偏差超过上限，按策略拒绝该操作。"""

    code = "clock_skew_rejected"


class GoalNotReadyError(IlluminationError):
    """目标未达到 L3 或反校验答否，不允许锁定。"""

    code = "goal_not_ready"

    def __init__(
        self,
        message: str,
        *,
        missing_fields: tuple[str, ...] = (),
        stop_reasons: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.missing_fields = tuple(missing_fields)
        self.stop_reasons = tuple(stop_reasons)


class GoalQuotaExceededError(IlluminationError):
    """主位或副位名额已满；不自动替换已有目标。"""

    code = "goal_quota_exceeded"

    def __init__(self, message: str, *, role: str, limit: int) -> None:
        super().__init__(message)
        self.role = role
        self.limit = limit


class GoalLockRejected(IlluminationError):
    """锁定输入形状不合法；reason 为稳定标识。"""

    code = "goal_lock_rejected"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class CycleNotFoundError(IlluminationError):
    """单元不存在。"""

    code = "cycle_not_found"


class CycleActivationRejected(IlluminationError):
    """单元激活违反状态机、单 active 或提前窗口。"""

    code = "cycle_activation_rejected"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class CycleReviewRejected(IlluminationError):
    """周期评估违反状态机、宽限期或 append-only 约束。"""

    code = "cycle_review_rejected"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class EvidenceNotFoundError(IlluminationError):
    """证据不存在。"""

    code = "evidence_not_found"


class EvidenceRejected(IlluminationError):
    """证据提交违反归属、观测项、状态或幂等一致性。"""

    code = "evidence_rejected"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class ActionNotFoundError(IlluminationError):
    """动作不存在。"""

    code = "action_not_found"


class ActionRejected(IlluminationError):
    """动作创建或完成违反观测项、周期状态等规则。"""

    code = "action_rejected"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason
