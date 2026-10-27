"""The SDK uses official, SHA-verified o200k BPE bytes shipped in the wheel."""

import base64
import hashlib
from functools import lru_cache
from importlib.metadata import version
from importlib.resources import files
from typing import Literal

import tiktoken
from pydantic import BaseModel, ConfigDict, Field

from zhiheng.shared.errors import AppError

SPEC_SHA256 = '1764e80429b8e033159307c9a2bf270a516406156c4bee22b0f87c6f9c6d0918'
ASSET_SHA256 = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'


class _EncodingSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    name: Literal['o200k_base']
    tiktoken_version: Literal['0.14.0']
    model_name: Literal['gpt-4o-mini']
    official_url: str
    asset_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    source_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    pat_str: str
    special_tokens: dict[str, int]
    rank_count: int


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    definition = files(__package__).joinpath('tokenizer_spec.json').read_bytes()
    if hashlib.sha256(definition).hexdigest() != SPEC_SHA256:
        raise AppError('graph_tokenizer_incompatible', '图谱分词配置指纹不匹配', 503)
    spec = _EncodingSpec.model_validate_json(definition)
    raw = files(__package__).joinpath('o200k_base.tiktoken').read_bytes()
    if (
        version('tiktoken') != spec.tiktoken_version
        or spec.asset_sha256 != ASSET_SHA256
        or hashlib.sha256(raw).hexdigest() != ASSET_SHA256
    ):
        raise AppError('graph_tokenizer_incompatible', '图谱离线分词资源版本或指纹不匹配', 503)
    ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in raw.splitlines() if line)}
    if len(ranks) != spec.rank_count:
        raise AppError('graph_tokenizer_incompatible', '图谱离线分词资源不完整', 503)
    return tiktoken.Encoding(
        name=spec.name, pat_str=spec.pat_str, mergeable_ranks=ranks, special_tokens=spec.special_tokens
    )


class OfficialBPETokenizer:
    """Immutable CoreBPE supports concurrent encode/decode without cache HTTP."""

    def __init__(self) -> None:
        self._encoding = _encoding()

    def __deepcopy__(self, memo: dict[int, object]) -> 'OfficialBPETokenizer':
        return self

    def encode(self, content: str) -> list[int]:
        return self._encoding.encode(content, disallowed_special=())

    def decode(self, tokens: list[int]) -> str:
        return self._encoding.decode(tokens)
