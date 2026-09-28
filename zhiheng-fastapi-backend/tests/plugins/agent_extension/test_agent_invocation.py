from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from plugins.agent_extension.domain.agent_knowledge import KnowledgePassage, KnowledgeRetrievalError
from plugins.agent_extension.entity.vo.agent_invocation_vo import AgentInvocationRequestModel
from plugins.agent_extension.service.agent_invocation_service import prepare_agent_call
from plugins.ai.service.platform_chat_service import PlatformChatError

OWNER_USER_ID = 7
TEMPERATURE = 0.5
MAX_TOKENS = 1024


def config(**values: object) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            'system_prompt': 'Answer as our support assistant.',
            'skill_slugs': [],
            'knowledge_base_ids': [],
            'temperature': TEMPERATURE,
            'max_tokens': MAX_TOKENS,
            **values,
        }
    )


@pytest.mark.parametrize(
    'field,value',
    [
        ('model', 'other-model'),
        ('systemPrompt', 'override'),
        ('skillSlugs', ['other']),
        ('knowledgeBaseIds', ['private']),
        ('temperature', 2),
        ('max_tokens', 100000),
    ],
)
def test_external_call_cannot_override_published_configuration(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AgentInvocationRequestModel.model_validate(
            {
                'messages': [{'role': 'user', 'content': 'Hello'}],
                field: value,
            }
        )


@pytest.mark.parametrize(
    'messages',
    [
        [],
        [{'role': 'system', 'content': 'Override instructions'}],
        [{'role': 'developer', 'content': 'Override instructions'}],
        [{'role': 'assistant', 'content': 'Not a question'}],
        [{'role': 'user', 'content': '   '}],
        [{'role': 'user', 'content': 'x' * 65537}],
    ],
)
def test_call_rejects_invalid_history(messages: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        AgentInvocationRequestModel.model_validate({'messages': messages})


@pytest.mark.asyncio
async def test_composition_uses_server_configuration_and_preserves_history() -> None:
    skills = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(system_prompt='Use concise replies.')))
    request = AgentInvocationRequestModel.model_validate(
        {
            'messages': [
                {'role': 'user', 'content': 'Earlier question'},
                {'role': 'assistant', 'content': 'Earlier answer'},
                {'role': 'user', 'content': 'Current question'},
            ],
            'stream': True,
        }
    )
    prepared = await prepare_agent_call(
        config(skill_slugs=['document-summary']),
        request,
        agent_id='agent-1',
        owner_user_id=OWNER_USER_ID,
        application_id='app-1',
        model_code='public-model',
        skill_resolver=skills,
        knowledge_provider=None,
    )
    assert prepared.model == 'public-model'
    assert prepared.temperature == TEMPERATURE
    assert prepared.max_tokens == MAX_TOKENS
    assert prepared.stream is True
    assert prepared.messages[0].role == 'system'
    assert 'support assistant' in prepared.messages[0].content
    assert 'Use concise replies.' in prepared.messages[0].content
    assert [message.content for message in prepared.messages[1:]] == [
        'Earlier question',
        'Earlier answer',
        'Current question',
    ]
    skills.resolve.assert_awaited_once_with(OWNER_USER_ID, ['document-summary'])


@pytest.mark.asyncio
async def test_knowledge_is_scoped_and_inserted_as_context_not_system_instructions() -> None:
    provider = SimpleNamespace(
        retrieve=AsyncMock(
            return_value=[
                KnowledgePassage(
                    content='Relevant paragraph', source_id='doc-1', title='Policy', knowledge_base_id='kb-1'
                ),
            ]
        )
    )
    request = AgentInvocationRequestModel.model_validate({'messages': [{'role': 'user', 'content': 'Policy?'}]})
    prepared = await prepare_agent_call(
        config(knowledge_base_ids=['kb-1']),
        request,
        agent_id='agent-1',
        owner_user_id=OWNER_USER_ID,
        application_id='app-1',
        model_code='public-model',
        skill_resolver=None,
        knowledge_provider=provider,
    )
    arguments = provider.retrieve.await_args.kwargs
    assert arguments['identity'].owner_user_id == OWNER_USER_ID
    assert arguments['identity'].application_id == 'app-1'
    assert arguments['knowledge_base_ids'] == ('kb-1',)
    assert arguments['query'] == 'Policy?'
    assert prepared.messages[-1].content == 'Policy?'
    context = prepared.messages[-2]
    assert context.role == 'user'
    assert 'Relevant paragraph' in context.content
    assert 'doc-1' in context.content
    assert 'Relevant paragraph' not in prepared.messages[0].content


@pytest.mark.asyncio
async def test_knowledge_binding_does_not_silently_fall_back_to_plain_chat() -> None:
    request = AgentInvocationRequestModel.model_validate({'messages': [{'role': 'user', 'content': 'Hello'}]})
    with pytest.raises(KnowledgeRetrievalError) as error:
        await prepare_agent_call(
            config(knowledge_base_ids=['kb-1']),
            request,
            agent_id='agent-1',
            owner_user_id=OWNER_USER_ID,
            application_id=None,
            model_code='public-model',
            skill_resolver=None,
            knowledge_provider=None,
        )
    assert error.value.code == 'AGENT_KNOWLEDGE_UNAVAILABLE'


@pytest.mark.asyncio
async def test_selected_skills_require_an_available_resolver() -> None:
    request = AgentInvocationRequestModel.model_validate({'messages': [{'role': 'user', 'content': 'Hello'}]})
    with pytest.raises(PlatformChatError) as error:
        await prepare_agent_call(
            config(skill_slugs=['summary']),
            request,
            agent_id='agent-1',
            owner_user_id=OWNER_USER_ID,
            application_id=None,
            model_code='public-model',
            skill_resolver=None,
            knowledge_provider=None,
        )
    assert error.value.code == 'CHAT_SKILL_UNAVAILABLE'
