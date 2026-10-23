"""知识库持久模型引用及新增引用和模型删除之间的跨进程协调。"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Concatenate, ParamSpec, Protocol, TypeVar

from plugins.ai.knowledge.metadata import MetadataStore
from plugins.ai.knowledge.models import KnowledgeError

MODEL_REFERENCE_LOCK = 'model-references'
_MODEL_FIELDS = frozenset({'embed_model_id', 'llm_model_id', 'model_id', 'platform_model_id'})
P = ParamSpec('P')
R = TypeVar('R')


class MetadataOwner(Protocol):
    metadata: MetadataStore


Owner = TypeVar('Owner', bound=MetadataOwner)


def model_reference_write(
    operation: Callable[Concatenate[Owner, P], Awaitable[R]],
) -> Callable[Concatenate[Owner, P], Awaitable[R]]:
    """从模型有效性检查开始持锁至引用持久化，防止删除检查与新引用竞态。"""

    @wraps(operation)
    async def guarded(owner: Owner, *args: P.args, **kwargs: P.kwargs) -> R:
        async with owner.metadata.lock(MODEL_REFERENCE_LOCK, shared=True):
            return await operation(owner, *args, **kwargs)

    return guarded


def _contains_reference(value: object, model_id: str) -> bool:
    if isinstance(value, dict):
        return any(
            (key in _MODEL_FIELDS and item == model_id) or _contains_reference(item, model_id)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_reference(item, model_id) for item in value)
    return False


async def has_model_references(metadata: MetadataStore, model_id: str) -> bool:
    """当前数据库配置和保留的版本/预览/索引快照都属于持久引用。"""
    state = await metadata.read()
    for database in state['databases'].values():
        if not isinstance(database, dict):
            raise KnowledgeError.unavailable()
        if _contains_reference(database, model_id):
            return True
    return False
