"""Lossless display escaping for text values PostgreSQL cannot store directly."""

SURROGATE_START = 0xD800
SURROGATE_END = 0xDFFF


def storage_text(value: str) -> str:
    return ''.join(
        f'\\u{ord(character):04x}'
        if character == '\x00' or SURROGATE_START <= ord(character) <= SURROGATE_END
        else character
        for character in value
    )
