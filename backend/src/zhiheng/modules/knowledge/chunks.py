import hashlib
import math
import struct
import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

MAX_CHUNKS = 2000
MAX_DIMENSION = 65536
MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 4000
MAX_OVERLAP = 1000


@dataclass(frozen=True)
class TextChunk:
    content: str
    model_text: str
    content_hash: str


def split_chunks(text: str, size: int, overlap: int) -> list[TextChunk]:
    if (
        not text.strip()
        or not MIN_CHUNK_SIZE <= size <= MAX_CHUNK_SIZE
        or not 0 <= overlap < size
        or overlap > MAX_OVERLAP
    ):
        raise ValueError('Invalid chunk input')
    if any(unicodedata.category(char) == 'Cc' and not char.isspace() for char in text):
        raise ValueError('Invalid control character')
    result = []
    offset = 0
    while offset < len(text):
        end = min(offset + size, len(text))
        boundary = text.rfind('\n', offset + size // 2, end)
        if end < len(text) and boundary >= 0:
            end = boundary + 1
        content = text[offset:end]
        if content.strip():
            result.append(TextChunk(content, ' '.join(content.split()), hashlib.sha256(content.encode()).hexdigest()))
        if len(result) > MAX_CHUNKS:
            raise ValueError('Too many chunks')
        if end == len(text):
            break
        offset = max(offset + 1, end - overlap)
    return result


class VectorRow(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
    index: int = Field(strict=True, ge=0)
    embedding: list[float] = Field(min_length=1, max_length=MAX_DIMENSION)


class VectorResponse(BaseModel):
    data: list[VectorRow]


def validate_vectors(result: object, count: int, dimension: int | None = None) -> list[list[float]]:
    rows = sorted(VectorResponse.model_validate(result).data, key=lambda row: row.index)
    if [row.index for row in rows] != list(range(count)) or not rows:
        raise ValueError('Invalid embedding indexes')
    vectors = [row.embedding for row in rows]
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or (dimension is not None and dimensions != {dimension}):
        raise ValueError('Invalid embedding dimensions')
    if any(not any(vector) or any(not math.isfinite(value) for value in vector) for vector in vectors):
        raise ValueError('Invalid embedding values')
    for vector in vectors:
        float32_vector(vector)
    return vectors


def float32_vector(vector: list[float]) -> bytes:
    try:
        packed = struct.pack('<' + 'f' * len(vector), *vector)
        values = struct.unpack('<' + 'f' * len(vector), packed)
    except (OverflowError, struct.error):
        raise ValueError('Embedding cannot be stored as FLOAT_VECTOR') from None
    if not any(values) or any(not math.isfinite(value) for value in values):
        raise ValueError('Embedding is zero or nonfinite after FLOAT_VECTOR conversion')
    return packed


def same_vector(actual: object, expected: object) -> bool:
    if not isinstance(actual, list) or not isinstance(expected, list) or len(actual) != len(expected):
        return False
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) for value in (*actual, *expected)):
        return False
    try:
        return float32_vector(actual) == float32_vector(expected)
    except ValueError:
        return False
