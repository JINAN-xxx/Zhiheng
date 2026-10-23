from typing import Protocol

from pydantic import BaseModel, ConfigDict

PARSER_VERSION = 'native-1'
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 2_000_000
MAX_IMAGE_URL_BYTES = 2_000_000


class ParseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    text: str
    metadata: dict[str, str | int]
    parser_version: str = PARSER_VERSION
    image_data: str | None = None
    source_sha256: str | None = None


class ParsePort(Protocol):
    async def parse(self, content: bytes, *, filename: str) -> ParseResult: ...
