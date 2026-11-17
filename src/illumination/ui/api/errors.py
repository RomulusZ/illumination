"""Consistent HTTP error codes with no exception details or secrets."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from illumination.service.errors import IlluminationError


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


_STATUS_BY_CODE = {
    "authentication_required": 401,
    "invalid_invite_code": 401,
    "csrf_validation_failed": 403,
    "clock_skew_rejected": 422,
    "goal_not_ready": 409,
    "goal_quota_exceeded": 409,
    "intake_turn_limit_exceeded": 409,
    "goal_lock_rejected": 422,
    "goal_not_found": 404,
    "goal_transition_rejected": 409,
    "cycle_not_found": 404,
    "cycle_activation_rejected": 409,
    "cycle_review_rejected": 409,
    "evidence_not_found": 404,
    "evidence_rejected": 422,
    "action_not_found": 404,
    "action_rejected": 422,
    "structured_output_invalid": 502,
    "planning_invalid": 422,
    "configuration_invalid": 500,
}

_SAFE_MESSAGES = {
    "authentication_required": "请先登录",
    "invalid_invite_code": "邀请码无效",
    "csrf_validation_failed": "CSRF 校验失败",
    "goal_not_ready": "目标尚未达到锁定条件",
}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(IlluminationError)
    async def domain_error(_request: Request, exc: IlluminationError) -> JSONResponse:
        return _error(
            _STATUS_BY_CODE.get(exc.code, 500),
            exc.code,
            _SAFE_MESSAGES.get(exc.code, "请求无法完成"),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_request", "请求参数不合法")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _error(exc.status_code, code, "请求无法完成")

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(500, "internal_error", "服务器内部错误")
