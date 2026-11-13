"""A fresh interpreter must be able to consume contracts without loading SDKs."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ('module', 'symbol'),
    [('knowledge', 'Hit'), ('models', 'ModelBinding')],
)
def test_public_contract_import_does_not_load_supplier_adapters(module: str, symbol: str) -> None:
    script = f"""
import importlib
import sys

public = importlib.import_module('zhiheng.modules.{module}.public')
assert getattr(public, {symbol!r}) is not None
forbidden = ('lightrag', 'numpy', 'pymilvus', 'grpc', 'langchain', 'openai',
             'zhiheng.modules.knowledge.adapters', 'zhiheng.modules.models.adapters')
loaded = sorted(name for name in sys.modules if any(
    name == prefix or name.startswith(prefix + '.') or name.startswith(prefix + '_')
    for prefix in forbidden
))
assert not loaded, 'Contract import loaded supplier adapters: ' + ', '.join(loaded)
"""
    result = subprocess.run(
        [sys.executable, '-B', '-c', script],
        env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_knowledge_public_exports_capabilities_without_sdk_constructors() -> None:
    from zhiheng.modules.knowledge import public

    assert {'ParsePort', 'VectorPort', 'GraphPort', 'GraphSDKCall', 'GraphSDKOutcome'} <= set(public.__all__)
    for name in ('NativeParser', 'MilvusStore', 'GraphSDK', 'SDKCall', 'SDKOutcome'):
        assert not hasattr(public, name), name
