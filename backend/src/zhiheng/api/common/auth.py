"""Strict shared Bearer transport parsing; authorization belongs to application services."""

import re

from fastapi import Request

from zhiheng.shared.errors import AppError


def bearer(request: Request) -> str:
    values = request.headers.getlist('authorization')
    if len(values) != 1 or re.fullmatch(r'Bearer sk-zh-[A-Za-z0-9_-]{43}', values[0]) is None:
        raise AppError('invalid_api_key', 'Invalid API key', 401)
    return values[0][7:]
