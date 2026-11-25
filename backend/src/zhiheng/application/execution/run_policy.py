"""Ownership and processing policies for every persisted Run resource."""

from dataclasses import dataclass
from typing import Literal, cast

from zhiheng.runtime.contracts import RunResourceType
from zhiheng.shared.errors import AppError

RunGuard = Literal[
    'chat',
    'model_test',
    'public_key',
    'assistant',
    'assistant_key',
    'work_turn',
    'workspace_parent',
    'workspace_model',
    'knowledge_step',
    'graph',
]


@dataclass(frozen=True)
class RunPolicy:
    guard: RunGuard
    projection: Literal['chat', 'work_turn', 'work_agent', 'none']
    lane: Literal['by_model', 'short', 'workspace']
    check_platform: bool = False
    check_prepared_knowledge: bool = False


_POLICIES: dict[RunResourceType, RunPolicy] = {
    'chat': RunPolicy('chat', 'chat', 'by_model', check_platform=True),
    'chat_summary': RunPolicy('chat', 'none', 'short', check_platform=True),
    'connection_test': RunPolicy('model_test', 'none', 'by_model'),
    'public': RunPolicy('public_key', 'none', 'by_model'),
    'agent_preview': RunPolicy('assistant', 'none', 'by_model', check_platform=True, check_prepared_knowledge=True),
    'agent_chat': RunPolicy('assistant', 'none', 'by_model', check_platform=True),
    'agent_summary': RunPolicy('assistant', 'none', 'short', check_platform=True),
    'agent_invocation': RunPolicy(
        'assistant_key', 'none', 'by_model', check_platform=True, check_prepared_knowledge=True
    ),
    'work_turn': RunPolicy('work_turn', 'work_turn', 'by_model', check_platform=True, check_prepared_knowledge=True),
    'work_agent': RunPolicy('workspace_parent', 'work_agent', 'workspace'),
    'work_agent_model': RunPolicy('workspace_model', 'none', 'by_model'),
    'knowledge_embedding': RunPolicy('knowledge_step', 'none', 'by_model'),
    'knowledge_rerank': RunPolicy('knowledge_step', 'none', 'by_model'),
    'knowledge_graph': RunPolicy('graph', 'none', 'by_model'),
}


def run_policy(resource_type: str) -> RunPolicy:
    policy = _POLICIES.get(cast('RunResourceType', resource_type))
    if policy is None:
        raise AppError('execution_resource_unsupported', '运行类型不受支持', 409)
    return policy
