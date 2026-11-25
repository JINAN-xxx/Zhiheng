from typing import get_args
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zhiheng.application.execution import run_policy as policies
from zhiheng.application.execution.authorization import ModelExecutionAuthorization
from zhiheng.runtime.contracts import ClaimedRun, RunResourceType
from zhiheng.shared.errors import AppError


def test_every_persisted_run_type_declares_its_execution_and_projection() -> None:
    assert set(policies._POLICIES) == set(get_args(RunResourceType))
    assert {kind for kind, policy in policies._POLICIES.items() if policy.lane == 'workspace'} == {'work_agent'}
    assert policies.run_policy('work_turn').projection == 'work_turn'
    assert policies.run_policy('agent_chat').projection == 'none'


@pytest.mark.parametrize('kind', ['unknown_kind', 'agent_invocation', 'work_agent'])
async def test_missing_or_misrouted_policy_fails_before_checking_claim_dependencies(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    if kind == 'agent_invocation':
        monkeypatch.delitem(policies._POLICIES, kind)
    service = ModelExecutionAuthorization.__new__(ModelExecutionAuthorization)
    check = AsyncMock()
    monkeypatch.setattr(service, '_require_platform', check)
    claim = ClaimedRun(uuid4(), kind, uuid4(), uuid4(), uuid4(), {})
    with pytest.raises(AppError) as result:
        await service.require(claim)
    assert result.value.code == 'execution_resource_unsupported'
    check.assert_not_awaited()
