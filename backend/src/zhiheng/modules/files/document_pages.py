"""Lossless, bounded text windows over parsed document blocks."""

import json
from typing import cast

from zhiheng.shared.errors import AppError

MAX_DOCUMENT_CHARACTERS = 131072
MAX_DOCUMENT_PAGE_BYTES = 512 * 1024
MAX_DOCUMENT_BLOCKS = 100000


def document_page(
    document: dict[str, object],
    start: int = 1,
    limit: int | None = None,
    *,
    offset: int = 0,
    max_characters: int = MAX_DOCUMENT_CHARACTERS,
) -> dict[str, object]:
    """Offsets count Unicode characters in the original block, including tables."""
    blocks = cast('list[str]', document['blocks'])
    base, total = int(cast('int', document['start'])), int(cast('int', document['total']))
    if (
        start < base
        or start > max(1, total)
        or offset < 0
        or (limit is not None and not 1 <= limit <= MAX_DOCUMENT_BLOCKS)
        or not 1 <= max_characters <= MAX_DOCUMENT_CHARACTERS
    ):
        raise AppError('invalid_file_page', '文档读取范围或续读位置无效', 422)
    index = start - base
    if index < len(blocks) and offset > len(blocks[index]):
        raise AppError('invalid_file_page', '段内续读位置超过原文长度', 422)
    end = min(len(blocks), index + limit) if limit is not None else len(blocks)
    items: list[dict[str, object]] = []
    remaining, used_bytes = max_characters, 2048
    next_block, next_offset = start, offset
    for number in range(index, end):
        text = blocks[number]
        begin = offset if number == index else 0
        length = min(len(text) - begin, remaining)
        # JSON escaping and per-item metadata also consume the transport budget.
        length = min(length, max(0, (MAX_DOCUMENT_PAGE_BYTES - used_bytes - 256) // 6))
        if length == 0 and begin < len(text):
            break
        item: dict[str, object] = {'number': base + number, 'text': text[begin : begin + length]}
        if begin or length < len(text):
            item.update(offset=begin, end_offset=begin + length, total_characters=len(text))
        items.append(item)
        used_bytes += len(json.dumps(item, ensure_ascii=False).encode()) + 2
        remaining -= length
        if begin + length < len(text):
            next_block, next_offset = base + number, begin + length
            break
        next_block, next_offset = base + number + 1, 0
        if used_bytes >= MAX_DOCUMENT_PAGE_BYTES - 2048:
            break
    more = next_block <= total
    return {
        'unit': document['unit'],
        'total': total,
        'items': items,
        'start': start,
        'offset': offset,
        'next': next_block if more else None,
        'next_offset': next_offset if more else None,
        'has_more': more,
        'complete': start == 1 and offset == 0 and not more,
        'truncated': more,
        'textless_pages': [
            item['number'] for item in items if document['unit'] == 'page' and not str(item['text']).strip()
        ],
        'note': '仅 complete=true 表示本次覆盖全文提取范围；续读使用 next 和 next_offset。PDF 无文字页可能需要 OCR，不能视为已读懂页面内容。',
    }
