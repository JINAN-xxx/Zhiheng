"""Input rules shared by package parsing, API contracts, and public selection."""

import re

from zhiheng.shared.errors import AppError

MAX_SLUG_LENGTH = 255
SLUG_PATTERN = r'^[a-z0-9]+(?:-[a-z0-9]+)*$'
MAX_SEARCH_LENGTH = 500
_SLUG = re.compile(SLUG_PATTERN)


def is_valid_slug(value: str) -> bool:
    return 0 < len(value) <= MAX_SLUG_LENGTH and _SLUG.fullmatch(value) is not None


def validate_search(value: str) -> None:
    if len(value) > MAX_SEARCH_LENGTH or '\x00' in value:
        raise AppError('invalid_skill_search', '技能搜索条件无效', 422)
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise AppError('invalid_skill_search', '技能搜索条件必须为有效UTF-8', 422) from error
