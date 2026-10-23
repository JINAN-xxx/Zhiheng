"""Bounded Markdown chunking adapted from f976247 knowledge ingestion.

Keeps paragraph boundaries and overlapping text without importing local model
packages. Chunk IDs identify a document revision so partial replacements cannot
be mistaken for the active index.
"""

import unicodedata

MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 4000
MAX_CHUNKS = 2000


def split_text_into_chunks(text: str, file_id: str, revision: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE or not 0 <= chunk_overlap < chunk_size:
        raise ValueError('无效的分块参数')
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = text.rfind('\n', start + chunk_size // 2, end)
            if boundary > start + chunk_overlap:
                end = boundary
        content = text[start:end].strip()
        if content:
            chunks.append({'id': f'{file_id}_{revision}_{len(chunks)}', 'doc_id': file_id, 'content': content})
        if len(chunks) > MAX_CHUNKS:
            raise ValueError('文档分块数量超过限制')
        if end == len(text):
            break
        start = max(start + 1, end - chunk_overlap)
    return chunks


def normalize_embedding_text(text: str) -> str:
    """Normalize model-only whitespace while preserving source documents."""
    normalized = []
    for character in text:
        if unicodedata.category(character) in {'Cc', 'Zl', 'Zp'}:
            if not character.isspace():
                raise ValueError('Unsupported control character')
            normalized.append(' ')
        else:
            normalized.append(character)
    return ''.join(normalized)
