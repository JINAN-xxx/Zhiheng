import re
from uuid import uuid4

ALLOWED_RESOURCE_PREFIXES = frozenset({'mdl', 'bnd', 'ses', 'msg', 'run'})


def _validate_prefix(prefix: str) -> None:
    if not isinstance(prefix, str):
        raise TypeError('资源 ID 前缀必须是字符串')
    if prefix not in ALLOWED_RESOURCE_PREFIXES:
        raise ValueError('不支持的资源 ID 前缀')


def new_resource_id(prefix: str) -> str:
    """生成不承载业务信息的 Platform 资源 ID。"""
    _validate_prefix(prefix)
    return f'{prefix}_{uuid4().hex}'


def validate_resource_id(value: str, prefix: str) -> str:
    """校验 Platform 资源 ID 类型前缀和 UUID4 紧凑格式。"""
    if not isinstance(value, str):
        raise TypeError('资源 ID 必须是字符串')
    _validate_prefix(prefix)
    if re.fullmatch(rf'{re.escape(prefix)}_[0-9a-f]{{32}}', value) is None:
        raise ValueError('资源 ID 格式无效')
    return value
