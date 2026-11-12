"""Bounded opaque keyset positions bound to the caller and collection filters."""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from zhiheng.shared.errors import AppError

MAX_CURSOR_LENGTH = 1024
CURSOR_COMPONENTS = 3


def encode_cursor(binding: str, created_at: datetime, resource_id: UUID) -> str:
    value = json.dumps([binding, created_at.isoformat(), str(resource_id)], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def decode_cursor(cursor: str | None, binding: str) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        if len(cursor) > MAX_CURSOR_LENGTH:
            raise ValueError
        value = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        if (
            not isinstance(value, list)
            or len(value) != CURSOR_COMPONENTS
            or any(not isinstance(part, str) for part in value)
            or value[0] != binding
        ):
            raise ValueError
        timestamp = datetime.fromisoformat(value[1])
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp, UUID(value[2])
    except (ValueError, TypeError, binascii.Error, OverflowError):
        raise AppError('invalid_cursor', '分页游标无效或不属于当前应用及过滤条件', 422) from None
