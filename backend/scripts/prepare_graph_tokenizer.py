"""Provision the verified graph vocabulary during deployment, before serving HTTP."""

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.ai.knowledge.graph_tokenizer import (
    GRAPH_TOKENIZER_CACHE_KEY,
    GRAPH_TOKENIZER_MAX_BYTES,
    GRAPH_TOKENIZER_SHA256,
    GRAPH_TOKENIZER_URL,
    validate_graph_tokenizer_cache,
)
from plugins.ai.knowledge.models import KnowledgeError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--source-cache', type=Path)
    options = parser.parse_args()
    try:
        validate_graph_tokenizer_cache(options.cache_dir)
    except KnowledgeError:
        if options.source_cache:
            content = validate_graph_tokenizer_cache(options.source_cache).read_bytes()
        else:
            with urlopen(GRAPH_TOKENIZER_URL, timeout=30) as response:
                content = response.read(GRAPH_TOKENIZER_MAX_BYTES + 1)
        if len(content) > GRAPH_TOKENIZER_MAX_BYTES or hashlib.sha256(content).hexdigest() != GRAPH_TOKENIZER_SHA256:
            raise ValueError('Graph vocabulary checksum mismatch') from None
        options.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=options.cache_dir, delete=False) as temporary:
            pending = Path(temporary.name)
            try:
                temporary.write(content)
                temporary.flush()
                os.fchmod(temporary.fileno(), 0o644)
                os.replace(pending, options.cache_dir / GRAPH_TOKENIZER_CACHE_KEY)
            finally:
                pending.unlink(missing_ok=True)
        validate_graph_tokenizer_cache(options.cache_dir)
    print('Graph vocabulary ready: o200k_base (SHA-256 verified)')


if __name__ == '__main__':
    main()
