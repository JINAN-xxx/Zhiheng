"""Shared validation for legacy history and goal-driven task values."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

MAX_TEXT_BYTES = 65536
Version = Annotated[StrictInt, Field(ge=1)]


def safe_text(value: str) -> str:
    try:
        encoded = value.encode('utf-8')
    except UnicodeError:
        raise ValueError('非法 Unicode') from None
    if '\x00' in value or len(encoded) > MAX_TEXT_BYTES:
        raise ValueError('文本包含非法字符或超过字节限制')
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
