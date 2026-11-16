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
