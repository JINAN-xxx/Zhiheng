# ruff: noqa: PLR2004
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from plugins.agent_extension.entity.vo.agent_vo import (
    AgentConfigModel,
    AgentConfigResponseModel,
    AgentId,
    AgentPageQueryModel,
    AgentPublishModel,
    AgentUpdateModel,
)

MODEL_ID = 'mdl_' + uuid4().hex
APPLICATION_ID = 'app_' + uuid4().hex


def config_payload(**overrides: object) -> dict[str, object]:
    return {
        'name': 'Research agent',
        'description': 'A configured assistant',
        'modelId': MODEL_ID,
        'systemPrompt': 'Be precise.',
        'skillSlugs': [],
        'knowledgeBaseIds': [],
        'applicationId': APPLICATION_ID,
        'temperature': 0.7,
        'maxTokens': 2048,
        **overrides,
    }


def test_configuration_accepts_camel_case_and_preserves_prompt() -> None:
    config = AgentConfigModel(**config_payload(name='  Research agent  ', systemPrompt='  Exact prompt\n'))
    assert config.name == 'Research agent'
    assert config.system_prompt == '  Exact prompt\n'
    assert config.model_dump(by_alias=True)['modelId'] == MODEL_ID
    assert AgentUpdateModel(**config_payload(expectedVersion=2)).expected_version == 2
    assert AgentPublishModel(expectedVersion=2).expected_version == 2


def test_welcome_message_defaults_to_empty_for_requests_and_legacy_responses() -> None:
    assert AgentConfigModel(**config_payload()).welcome_message == ''
    assert AgentUpdateModel(**config_payload(expectedVersion=1)).welcome_message == ''
    legacy = AgentConfigResponseModel(**config_payload())
    assert legacy.welcome_message == ''
    assert legacy.model_dump(by_alias=True)['welcomeMessage'] == ''


@pytest.mark.parametrize(
    'welcome_message',
    ['', '  欢迎使用！\n<script>plain text</script>  ', '你' * 2000],
    ids=['empty', 'literal_text', 'max_length'],
)
def test_welcome_message_accepts_bounded_plain_text_without_modifying_it(welcome_message: str) -> None:
    config = AgentConfigModel(**config_payload(welcomeMessage=welcome_message))
    assert config.welcome_message == welcome_message
    assert config.model_dump(by_alias=True)['welcomeMessage'] == welcome_message
    update = AgentUpdateModel(**config_payload(welcomeMessage=welcome_message, expectedVersion=1))
    assert update.welcome_message == welcome_message


@pytest.mark.parametrize(
    'welcome_message', [None, True, 1, [], {}, '你' * 2001], ids=['null', 'bool', 'int', 'list', 'dict', 'too_long']
)
@pytest.mark.parametrize('model', [AgentConfigModel, AgentUpdateModel])
def test_welcome_message_rejects_non_strings_and_overlong_values(
    welcome_message: object, model: type[AgentConfigModel]
) -> None:
    overrides = {'welcomeMessage': welcome_message}
    if model is AgentUpdateModel:
        overrides['expectedVersion'] = 1
    with pytest.raises(ValidationError) as invalid:
        model(**config_payload(**overrides))
    assert invalid.value.errors()[0]['type'] in {'string_type', 'string_too_long'}


@pytest.mark.parametrize(
    'overrides',
    [
        {'name': '   '},
        {'modelId': 'not-a-model'},
        {'applicationId': 'other'},
        {'temperature': True},
        {'temperature': '0.7'},
        {'temperature': float('nan')},
        {'temperature': 2.1},
        {'maxTokens': True},
        {'maxTokens': 0},
        {'skillSlugs': ['a', 'a']},
        {'skillSlugs': ['Invalid_Slug']},
        {'knowledgeBaseIds': ['kb', 'kb']},
        {'knowledgeBaseIds': ['']},
        {'ownerUserId': 2},
        {'model_id': MODEL_ID},
        {'system_prompt': 'override'},
        {'welcome_message': 'override'},
    ],
)
def test_configuration_rejects_invalid_or_extra_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentConfigModel(**config_payload(**overrides))


@pytest.mark.parametrize('version', [0, True, '1'])
def test_versions_are_positive_strict_integers(version: object) -> None:
    with pytest.raises(ValidationError):
        AgentPublishModel(expectedVersion=version)


def test_agent_identifier_is_a_canonical_uuid4_string() -> None:
    adapter = TypeAdapter(AgentId)
    agent_id = str(uuid4())
    assert adapter.validate_python(agent_id) == agent_id
    for invalid in ['agent-1', uuid4().hex, agent_id.upper(), '00000000-0000-0000-0000-000000000000']:
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_page_parameters_are_bounded_and_parse_http_strings() -> None:
    assert AgentPageQueryModel(pageNum='2', pageSize='10').page_num == 2
    for overrides in [{'pageNum': True}, {'pageSize': 101}, {'page_num': 1}]:
        with pytest.raises(ValidationError):
            AgentPageQueryModel(**overrides)
