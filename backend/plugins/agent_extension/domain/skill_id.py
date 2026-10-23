import re
from uuid import RFC_4122, UUID, uuid4

SKILL_ID_PREFIX = 'skl'
UUID4_VERSION = 4


def new_skill_id() -> str:
    return f'{SKILL_ID_PREFIX}_{uuid4().hex}'


def validate_skill_id(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError('技能 ID 必须是字符串')
    match = re.fullmatch(rf'{SKILL_ID_PREFIX}_([0-9a-f]{{32}})', value)
    if match is None:
        raise ValueError('技能 ID 格式无效')
    parsed = UUID(hex=match.group(1))
    if parsed.version != UUID4_VERSION or parsed.variant != RFC_4122:
        raise ValueError('技能 ID 必须使用 RFC 4122 UUID4')
    return value
