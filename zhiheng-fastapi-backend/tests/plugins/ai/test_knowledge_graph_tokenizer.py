import hashlib
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from plugins.ai.knowledge.models import KnowledgeError


def module() -> ModuleType:
    try:
        return importlib.import_module('plugins.ai.knowledge.graph_tokenizer')
    except ModuleNotFoundError:
        pytest.fail('Graph tokenizer must reject missing cache instead of downloading during a request')


@pytest.mark.parametrize('cache_state', ['missing', 'corrupt', 'disabled'])
def test_graph_tokenizer_fails_closed_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_state: str
) -> None:
    tokenizer = module()
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR', '' if cache_state == 'disabled' else str(tmp_path))
    if cache_state == 'corrupt':
        (tmp_path / tokenizer.GRAPH_TOKENIZER_CACHE_KEY).write_bytes(b'invalid vocabulary')
    utils = ModuleType('lightrag.utils')
    utils.TiktokenTokenizer = lambda _: pytest.fail('Invalid cache must not invoke the network-capable tokenizer')
    monkeypatch.setitem(sys.modules, 'lightrag', ModuleType('lightrag'))
    monkeypatch.setitem(sys.modules, 'lightrag.utils', utils)
    with pytest.raises(KnowledgeError) as caught:
        tokenizer.load_graph_tokenizer()
    assert caught.value.code == 'KNOWLEDGE_GRAPH_UNAVAILABLE'


def test_valid_cache_is_checked_before_constructing_tokenizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tokenizer = module()
    content = b'validated test vocabulary'
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(tokenizer, 'GRAPH_TOKENIZER_SHA256', hashlib.sha256(content).hexdigest())
    (tmp_path / tokenizer.GRAPH_TOKENIZER_CACHE_KEY).write_bytes(content)
    expected = SimpleNamespace(model_name='gpt-4o-mini')
    utils = ModuleType('lightrag.utils')
    utils.TiktokenTokenizer = lambda model_name: expected if model_name == expected.model_name else None
    monkeypatch.setitem(sys.modules, 'lightrag', ModuleType('lightrag'))
    monkeypatch.setitem(sys.modules, 'lightrag.utils', utils)
    assert tokenizer.load_graph_tokenizer() is expected


def test_tokenizer_cache_has_a_bounded_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tokenizer = module()
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(tokenizer, 'GRAPH_TOKENIZER_MAX_BYTES', 4)
    content = b'too long'
    monkeypatch.setattr(tokenizer, 'GRAPH_TOKENIZER_SHA256', hashlib.sha256(content).hexdigest())
    (tmp_path / tokenizer.GRAPH_TOKENIZER_CACHE_KEY).write_bytes(content)
    with pytest.raises(KnowledgeError):
        tokenizer.load_graph_tokenizer()
