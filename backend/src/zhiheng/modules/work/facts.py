"""JSONB-safe execution displays; original code arguments and file bytes stay unchanged."""

from typing import cast

from zhiheng.shared.errors import AppError

C0_END = 32
C1_START = 127
C1_END = 159
SURROGATE_START = 0xD800
SURROGATE_END = 0xDFFF


def visible_work_text(value: str) -> str:
    def forbidden(character: str) -> bool:
        number = ord(character)
        return (
            (number < C0_END and character not in '\n\r\t')
            or C1_START <= number <= C1_END
            or SURROGATE_START <= number <= SURROGATE_END
        )

    if not any(forbidden(character) for character in value):
        return value
    return ''.join(
        f'\\u{ord(character):04x}' if forbidden(character) else '\\\\' if character == '\\' else character
        for character in value
    )


def _normalize_fact_value(value: object) -> object:
    if isinstance(value, str):
        return visible_work_text(value)
    if isinstance(value, (list, tuple)):
        return [_normalize_fact_value(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AppError('invalid_work_fact', '执行记录字段必须是字符串', 422)
            normalized = visible_work_text(key)
            if normalized in result:
                raise AppError('invalid_work_fact', '执行记录字段转义后重复', 422)
            result[normalized] = _normalize_fact_value(item)
        return result
    return value


def normalize_work_fact(value: dict[str, object]) -> dict[str, object]:
    return cast('dict[str, object]', _normalize_fact_value(value))
