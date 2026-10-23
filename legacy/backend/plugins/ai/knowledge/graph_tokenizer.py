"""Load the graph tokenizer only from verified, pre-provisioned vocabulary data."""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from plugins.ai.knowledge.models import KnowledgeError

# Immutable vocabulary and digest used by tiktoken's o200k_base constructor.
GRAPH_TOKENIZER_URL = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
GRAPH_TOKENIZER_SHA256 = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'
GRAPH_TOKENIZER_CACHE_KEY = hashlib.sha1(GRAPH_TOKENIZER_URL.encode()).hexdigest()
GRAPH_TOKENIZER_MAX_BYTES = 5 * 1024 * 1024


def validate_graph_tokenizer_cache(directory: Path | None = None) -> Path:
    if directory is None:
        configured = os.environ.get(
            'TIKTOKEN_CACHE_DIR',
            os.environ.get('DATA_GYM_CACHE_DIR', str(Path(tempfile.gettempdir()) / 'data-gym-cache')),
        )
        if not configured:
            raise KnowledgeError('KNOWLEDGE_GRAPH_UNAVAILABLE', '知识图谱处理资源尚未就绪，请联系管理员', 503)
        directory = Path(configured)
    cached = directory / GRAPH_TOKENIZER_CACHE_KEY
    try:
        with cached.open('rb') as stream:
            content = stream.read(GRAPH_TOKENIZER_MAX_BYTES + 1)
        if len(content) > GRAPH_TOKENIZER_MAX_BYTES or hashlib.sha256(content).hexdigest() != GRAPH_TOKENIZER_SHA256:
            raise ValueError('Invalid graph vocabulary')
    except (OSError, ValueError):
        raise KnowledgeError('KNOWLEDGE_GRAPH_UNAVAILABLE', '知识图谱处理资源尚未就绪，请联系管理员', 503) from None
    return cached


def load_graph_tokenizer() -> Any:
    # tiktoken otherwise performs an unbounded synchronous HTTP download. Never
    # allow a missing/corrupt deployment resource to freeze the application loop.
    validate_graph_tokenizer_cache()
    from lightrag.utils import TiktokenTokenizer  # noqa: PLC0415

    return TiktokenTokenizer('gpt-4o-mini')
