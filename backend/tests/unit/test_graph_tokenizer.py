import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from zhiheng.modules.knowledge.adapters.lightrag.tokenizer import OfficialBPETokenizer


def test_official_packaged_bpe_is_thread_safe_and_matches_reference_tokens() -> None:
    expected = json.loads((Path(__file__).resolve().parents[1] / 'core/graph_token_ids.json').read_text())
    tokenizer = OfficialBPETokenizer()
    assert deepcopy(tokenizer) is tokenizer
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(tokenizer.encode, list(expected) * 8))
    assert values == list(expected.values()) * 8
    for text in expected:
        assert tokenizer.decode(tokenizer.encode(text)) == text


def test_empty_tokenizer_cache_and_blocked_http_need_no_download(tmp_path: Path) -> None:
    cache = tmp_path / 'empty-cache'
    cache.mkdir()
    env = {**os.environ, 'TIKTOKEN_CACHE_DIR': str(cache), 'DATA_GYM_CACHE_DIR': str(cache)}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    script = """
import requests,tiktoken,tiktoken.load
from copy import deepcopy
from zhiheng.modules.knowledge.adapters.lightrag.tokenizer import OfficialBPETokenizer

def blocked(*args,**kwargs):raise AssertionError('Unexpected tokenizer HTTP/cache registry access')
requests.get=blocked
requests.sessions.Session.request=blocked
tiktoken.load.read_file=blocked
tiktoken.get_encoding=blocked
tokenizer=OfficialBPETokenizer()
assert tokenizer.decode(tokenizer.encode('离线真实BPE。'))=='离线真实BPE。'
assert deepcopy(tokenizer) is tokenizer
"""
    result = subprocess.run(
        [sys.executable, '-B', '-c', script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert list(cache.iterdir()) == []
