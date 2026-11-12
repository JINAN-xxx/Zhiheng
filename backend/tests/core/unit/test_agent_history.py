from uuid import uuid4

import pytest
from pydantic import ValidationError

from zhiheng.modules.agents import public as agents
from zhiheng.modules.models.public import ModelMessage


def test_legacy_agent_config_defaults_to_twenty_history_rounds() -> None:
    config = agents.AgentConfig(name='Legacy', model_id=uuid4())
    assert config.history_rounds == 20


@pytest.mark.parametrize('rounds', [0, 1, 20])
def test_agent_history_rounds_accepts_integer_limits(rounds: int) -> None:
    config = agents.AgentConfig(name='Agent', model_id=uuid4(), history_rounds=rounds)
    assert config.history_rounds == rounds


@pytest.mark.parametrize('rounds', [True, False, 1.0, 1.5, -1, 21, '1', None])
def test_agent_history_rounds_rejects_non_integer_or_out_of_range(rounds: object) -> None:
    with pytest.raises(ValidationError):
        agents.AgentConfig.model_validate({'name': 'Agent', 'model_id': uuid4(), 'history_rounds': rounds})


@pytest.mark.parametrize('rounds', [0, 1, 20])
def test_select_agent_history_keeps_latest_complete_rounds_and_current_user(rounds: int) -> None:
    messages = (
        *(ModelMessage(role, f'{index}:{role}') for index in range(22) for role in ('user', 'assistant')),
        ModelMessage('user', 'current'),
    )
    assert hasattr(agents, 'select_agent_history'), 'Agent history selector must be a public agent boundary'
    selected = agents.select_agent_history(messages, rounds)
    assert selected == messages[-(rounds * 2 + 1) :]
    assert len(messages) == 45


def test_agent_history_groups_assistant_prefix_and_skips_unanswered_users() -> None:
    messages = tuple(
        ModelMessage(role, content)
        for role, content in [
            ('assistant', 'legacy prefix'),
            ('assistant', 'legacy continuation'),
            ('user', 'unanswered'),
            ('user', 'answered'),
            ('assistant', 'answer'),
            ('assistant', 'continuation'),
            ('user', 'also unanswered'),
            ('user', 'current'),
        ]
    )
    assert hasattr(agents, 'select_agent_history'), 'Agent history selector must be a public agent boundary'
    assert agents.select_agent_history(messages, 1) == (*messages[3:6], messages[-1])
    assert agents.select_agent_history(messages, 2) == (*messages[:2], *messages[3:6], messages[-1])


def test_agent_history_size_caps_drop_whole_rounds_and_keep_current_user() -> None:
    messages = (
        ModelMessage('user', 'old'),
        ModelMessage('assistant', 'old answer'),
        ModelMessage('user', '新'),
        ModelMessage('assistant', '回答'),
        ModelMessage('user', 'current'),
    )
    assert hasattr(agents, 'select_agent_history'), 'Agent history selector must be a public agent boundary'
    assert agents.select_agent_history(messages, 20, max_messages=4) == messages[2:]
    assert agents.select_agent_history(messages, 20, max_bytes=16) == messages[2:]
    assert agents.select_agent_history(messages, 20, max_bytes=1) == messages[-1:]
