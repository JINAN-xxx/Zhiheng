"""One explicit SDK registry boundary, preserving the pinned implementation bytes."""

import hashlib
import json
import threading
from importlib.metadata import version
from importlib.resources import files

from lightrag import kg  # type: ignore[import-untyped]
from lightrag.utils import logger as sdk_logger  # type: ignore[import-untyped]
from zhiheng.shared.errors import AppError

_LOCK = threading.Lock()
_STORAGES = (
    ('GraphKVStorage', 'KV_STORAGE', 'zhiheng.modules.knowledge.adapters.lightrag.storage'),
    ('GraphDocStorage', 'DOC_STATUS_STORAGE', 'zhiheng.modules.knowledge.adapters.lightrag.storage'),
    ('GraphTableStorage', 'GRAPH_STORAGE', 'zhiheng.modules.knowledge.adapters.lightrag.storage'),
    ('GraphVectorStorage', 'VECTOR_STORAGE', 'zhiheng.modules.knowledge.adapters.lightrag.vector'),
)


def register() -> None:
    with _LOCK:
        sdk_logger.disabled = True
        if version('lightrag-hku') != '1.5.7':
            raise AppError('graph_sdk_incompatible', '图谱SDK版本不兼容', 503)
        expected = json.loads(files(__package__).joinpath('sdk_hashes.json').read_text())
        for path, digest in expected.items():
            if hashlib.sha256(files('lightrag').joinpath(path).read_bytes()).hexdigest() != digest:
                raise AppError('graph_sdk_incompatible', '图谱SDK字节校验失败', 503)
        for name, kind, module in _STORAGES:
            existing = kg.STORAGES.get(name)
            if existing is not None and existing != module:
                raise AppError('graph_registry_conflict', '图谱存储注册名冲突', 503)
            if existing is None:
                kg.STORAGE_IMPLEMENTATIONS[kind]['implementations'].append(name)
                kg.STORAGE_ENV_REQUIREMENTS[name] = []
                kg.STORAGES[name] = module
