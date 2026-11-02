"""Strong version preconditions shared by mutable platform resources."""

from fastapi import Request

from zhiheng.shared.errors import AppError

MAX_VERSION_DIGITS = 10


def expected_version(request: Request, kind: str) -> int:
    values = request.headers.getlist('if-match')
    if not values:
        raise AppError('precondition_required', '必须提供If-Match资源版本', 428)
    prefix = f'"{kind}-'
    if len(values) != 1 or not values[0].startswith(prefix) or not values[0].endswith('"'):
        raise AppError('invalid_precondition', '需要强资源版本ETag', 422)
    value = values[0][len(prefix) : -1]
    if not value.isascii() or not value.isdigit() or len(value) > MAX_VERSION_DIGITS or int(value) < 1:
        raise AppError('invalid_precondition', '资源版本ETag无效', 422)
    return int(value)


def idempotency_header(request: Request) -> None:
    if len(request.headers.getlist('idempotency-key')) != 1:
        raise AppError('invalid_idempotency_key', '必须提供单个UUID请求键', 422)
