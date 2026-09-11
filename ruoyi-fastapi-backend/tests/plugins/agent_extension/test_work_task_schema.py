# ruff: noqa: PLR2004
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from config.database import Base
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTask, AgentWorkTaskMember
from plugins.agent_extension.entity.vo.work_task_vo import (
    WorkAgentOptionModel,
    WorkTaskCancelModel,
    WorkTaskCreateModel,
    WorkTaskDetailModel,
    WorkTaskMemberAddModel,
    WorkTaskMemberModel,
    WorkTaskMemberUpdateModel,
    WorkTaskMessageModel,
    WorkTaskModel,
    WorkTaskPageQueryModel,
    WorkTaskRunRequestModel,
    WorkTaskUpdateModel,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = BACKEND_ROOT / 'plugins' / 'agent_extension'
AGENT_ID_1 = str(uuid4())
AGENT_ID_2 = str(uuid4())


def _normalize_sql(sql: str) -> str:
    return re.sub(r'\s+', ' ', sql.lower()).strip()


def _check_constraints(table: object) -> dict[str, str]:
    return {
        constraint.name: _normalize_sql(str(constraint.sqltext))
        for constraint in table.constraints
        if getattr(constraint, 'name', None) and hasattr(constraint, 'sqltext')
    }


def _read_migration(database: str) -> str:
    return (PLUGIN_ROOT / 'migrations' / database / '004_work_tasks.sql').read_text(encoding='utf-8')


def test_work_task_schema_exposes_camel_case_and_rejects_invalid_state() -> None:
    task = WorkTaskModel(
        taskId='task-1',
        ownerUserId=10,
        title='发布资料',
        goal='整理发布资料',
        status='active',
        activeAgentId='agent-1',
        version=1,
    )

    assert task.model_dump(by_alias=True)['activeAgentId'] == 'agent-1'
    with pytest.raises(ValidationError):
        WorkTaskUpdateModel(expectedVersion=1, status='running')


def test_work_tables_define_idempotency_and_status_constraints() -> None:
    assert {'agent_work_task', 'agent_work_task_member', 'agent_work_message'} <= set(Base.metadata.tables)
    task = AgentWorkTask.__table__
    message = AgentWorkMessage.__table__

    assert any(constraint.name == 'uk_agent_work_task_create_client' for constraint in task.constraints)
    assert any(constraint.name == 'uk_agent_work_message_client' for constraint in message.constraints)


def test_work_requests_are_strict_camel_case_and_validate_content_and_agents() -> None:
    create = WorkTaskCreateModel(
        goal='  整理资料  ',
        agentIds=[AGENT_ID_1, AGENT_ID_2],
        activeAgentId=AGENT_ID_2,
        clientMessageId='  create-1  ',
    )
    assert create.goal == '整理资料'
    assert create.client_message_id == 'create-1'
    assert create.model_dump(by_alias=True)['activeAgentId'] == AGENT_ID_2

    invalid_create_overrides = (
        {'goal': '   '},
        {'goal': '你' * 32001},
        {'agentIds': []},
        {'agentIds': [str(uuid4()) for _ in range(11)]},
        {'agentIds': [AGENT_ID_1, AGENT_ID_1]},
        {'activeAgentId': str(uuid4())},
        {'clientMessageId': None},
        {'clientMessageId': '   '},
        {'clientMessageId': 'x' * 101},
        {'unknown': True},
        {'agent_ids': [AGENT_ID_1]},
    )
    for overrides in invalid_create_overrides:
        payload = {
            'goal': '整理资料',
            'agentIds': [AGENT_ID_1, AGENT_ID_2],
            'activeAgentId': AGENT_ID_1,
            'clientMessageId': 'create-1',
            **overrides,
        }
        with pytest.raises(ValidationError):
            WorkTaskCreateModel(**payload)

    run = WorkTaskRunRequestModel(agentId=AGENT_ID_1, clientMessageId='run-1', message='  开始整理  ')
    assert run.message == '开始整理'
    for overrides in ({'message': None}, {'message': '   '}, {'message': '你' * 32001}, {'extra': True}):
        with pytest.raises(ValidationError):
            WorkTaskRunRequestModel(
                **{'agentId': AGENT_ID_1, 'clientMessageId': 'run-1', 'message': 'hello', **overrides}
            )


@pytest.mark.parametrize(
    'payload',
    [
        {'expectedVersion': 1},
        {'expectedVersion': 1, 'title': '   '},
        {'expectedVersion': 1, 'title': None},
        {'expectedVersion': 1, 'title': 'x' * 101},
        {'expectedVersion': 1, 'goal': '   '},
        {'expectedVersion': 1, 'goal': None},
        {'expectedVersion': 1, 'goal': '你' * 32001},
        {'expectedVersion': 1, 'status': None},
        {'expectedVersion': 1, 'status': 'running'},
        {'expectedVersion': '1', 'title': '有效标题'},
    ],
)
def test_work_task_update_requires_a_valid_strict_change(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkTaskUpdateModel(**payload)


def test_work_responses_parse_attributes_and_reject_invalid_states() -> None:
    task = SimpleNamespace(
        task_id='task-1',
        owner_user_id=10,
        title='发布资料',
        goal='整理发布资料',
        status='active',
        active_agent_id='agent-1',
        version=1,
        created_at=None,
        updated_at=None,
    )
    response = WorkTaskModel.model_validate(task)
    assert response.model_dump(by_alias=True)['ownerUserId'] == 10
    with pytest.raises(ValidationError):
        WorkTaskModel.model_validate({**vars(task), 'status': 'running'})
    with pytest.raises(ValidationError):
        WorkTaskMessageModel(
            messageId='message-1',
            taskId='task-1',
            role='assistant',
            content='结果',
            runStatus='running',
        )
    message = WorkTaskMessageModel(
        messageId='message-1',
        taskId='task-1',
        role='assistant',
        content='结果',
        reasoningContent='推理过程',
        publishedVersion=3,
        runStatus='interrupted',
    )
    assert message.published_version == 3
    assert message.reasoning_content == '推理过程'


def test_work_detail_and_json_defaults_are_fresh() -> None:
    task_payload = {
        'taskId': 'task-1',
        'ownerUserId': 10,
        'title': '发布资料',
        'goal': '整理发布资料',
        'status': 'active',
        'version': 1,
    }
    first = WorkTaskDetailModel(**task_payload)
    second = WorkTaskDetailModel(**task_payload)
    assert first.members == [] and first.messages == []
    assert first.members is not second.members
    config_default = AgentWorkTaskMember.__table__.c.config_snapshot.default.arg
    assert callable(config_default)
    assert config_default(None) == {}
    assert config_default(None) is not config_default(None)


def test_work_auxiliary_models_expose_only_the_expected_camel_case_fields() -> None:
    assert WorkTaskMemberAddModel(expectedVersion=1, agentId=AGENT_ID_1).agent_id == AGENT_ID_1
    member_update = WorkTaskMemberUpdateModel(expectedVersion=1, expectedPublishedVersion=3)
    assert member_update.expected_published_version == 3
    with pytest.raises(ValidationError):
        WorkTaskMemberUpdateModel(expectedVersion=1, expectedPublishedVersion=3, activeAgentId=AGENT_ID_1)
    for version in (None, 0, True, '1'):
        with pytest.raises(ValidationError):
            WorkTaskMemberUpdateModel(expectedVersion=1, expectedPublishedVersion=version)
    assert WorkTaskCancelModel(runId='run-1').run_id == 'run-1'
    assert WorkTaskPageQueryModel(pageNum='2', pageSize='20').page_num == 2
    option = WorkAgentOptionModel(
        agentId='agent-1', name='资料助手', description='整理资料', status='published', publishedVersion=3
    )
    assert set(option.model_dump(by_alias=True)) == {
        'agentId',
        'name',
        'description',
        'status',
        'publishedVersion',
    }


def test_member_response_hides_config_and_reports_resource_availability() -> None:
    member = WorkTaskMemberModel.model_validate(
        SimpleNamespace(
            task_id='task-1',
            agent_id='agent-1',
            agent_name_snapshot='资料助手',
            published_version=2,
            config_snapshot={'systemPrompt': 'secret'},
            added_at=None,
            removed_at=None,
        )
    )
    payload = member.model_dump(by_alias=True)
    assert 'configSnapshot' not in payload
    assert payload['available'] is True
    assert payload['latestPublishedVersion'] is None


def test_work_entities_compile_with_matching_json_and_text_types() -> None:
    tables = (AgentWorkTask.__table__, AgentWorkTaskMember.__table__, AgentWorkMessage.__table__)
    mysql_ddl = ' '.join(_normalize_sql(str(CreateTable(table).compile(dialect=mysql.dialect()))) for table in tables)
    postgres_ddl = ' '.join(
        _normalize_sql(str(CreateTable(table).compile(dialect=postgresql.dialect()))) for table in tables
    )
    assert mysql_ddl.count('mediumtext') == 3
    assert 'config_snapshot json not null' in mysql_ddl
    assert 'usage_snapshot json' in mysql_ddl
    assert 'config_snapshot jsonb not null' in postgres_ddl
    assert 'usage_snapshot jsonb' in postgres_ddl
    assert 'agent_version' not in mysql_ddl + postgres_ddl
    assert re.search(r"reasoning_content mediumtext [^,]*default \(''\)", mysql_ddl)
    assert "reasoning_content text default ('') not null" in postgres_ddl


def test_work_message_sqlite_enforces_shapes_versions_and_idempotency() -> None:
    engine = create_engine('sqlite:///:memory:')
    AgentWorkTask.__table__.create(engine)
    AgentWorkMessage.__table__.create(engine)
    task_id = str(uuid4())
    user_message_id = str(uuid4())
    task_values = {
        'task_id': task_id,
        'owner_user_id': 10,
        'create_client_message_id': 'create-1',
        'title': '发布资料',
        'goal': '整理发布资料',
    }
    user_values = {
        'message_id': user_message_id,
        'task_id': task_id,
        'client_message_id': 'message-1',
        'role': 'user',
        'content': '开始整理',
    }
    assistant_values = {
        'message_id': str(uuid4()),
        'task_id': task_id,
        'reply_to_message_id': user_message_id,
        'role': 'assistant',
        'content': '整理完成',
        'agent_id': AGENT_ID_1,
        'agent_name_snapshot': '资料助手',
        'published_version': 2,
        'run_id': str(uuid4()),
        'run_status': 'interrupted',
    }
    with engine.begin() as connection:
        connection.execute(AgentWorkTask.__table__.insert(), task_values)
        connection.execute(AgentWorkMessage.__table__.insert(), user_values)
        connection.execute(AgentWorkMessage.__table__.insert(), assistant_values)

    invalid_messages = (
        {**user_values, 'message_id': str(uuid4()), 'client_message_id': 'bad-user', 'run_status': 'pending'},
        {
            **user_values,
            'message_id': str(uuid4()),
            'client_message_id': 'bad-reasoning',
            'reasoning_content': 'user messages cannot contain reasoning',
        },
        {
            **assistant_values,
            'message_id': str(uuid4()),
            'run_id': str(uuid4()),
            'client_message_id': 'bad-assistant',
        },
        {**assistant_values, 'message_id': str(uuid4()), 'run_id': str(uuid4()), 'published_version': 0},
        {**assistant_values, 'message_id': str(uuid4()), 'run_id': str(uuid4()), 'run_status': 'running'},
    )
    for values in invalid_messages:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(AgentWorkMessage.__table__.insert(), values)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(AgentWorkTask.__table__.insert(), {**task_values, 'task_id': str(uuid4())})


def test_work_task_migrations_define_equivalent_contracts() -> None:
    mysql_sql = _normalize_sql(_read_migration('mysql'))
    postgres_sql = _normalize_sql(_read_migration('postgresql'))

    for sql in (mysql_sql, postgres_sql):
        for table in ('agent_work_task', 'agent_work_task_member', 'agent_work_message'):
            assert f'create table if not exists {table}' in sql
        assert 'constraint uk_agent_work_task_create_client unique (owner_user_id, create_client_message_id)' in sql
        assert 'constraint uk_agent_work_message_client unique (task_id, client_message_id)' in sql
        assert 'constraint uk_agent_work_message_run unique (run_id)' in sql
        assert "check (status in ('active', 'completed', 'archived'))" in sql
        assert "check (role in ('user', 'assistant'))" in sql
        assert "'streaming'" in sql
        assert "'interrupted'" in sql
        assert 'foreign key (task_id) references agent_work_task (task_id) on delete restrict' in sql
        assert "role = 'user'" in sql and "role = 'assistant'" in sql
        assert "reasoning_content = ''" in sql
        assert 'published_version' in sql
        assert 'agent_version' not in sql
        assert 'reasoning_content' in sql
        assert 'drop table' not in sql

    assert 'config_snapshot json not null' in mysql_sql
    assert 'usage_snapshot json null' in mysql_sql
    assert 'created_at datetime not null' in mysql_sql
    assert mysql_sql.count('mediumtext') == 3
    assert "reasoning_content mediumtext not null default ('')" in mysql_sql
    assert 'engine=innodb' in mysql_sql
    assert 'config_snapshot jsonb not null' in postgres_sql
    assert 'usage_snapshot jsonb null' in postgres_sql
    assert 'created_at timestamp not null' in postgres_sql
    assert "reasoning_content text not null default ('')" in postgres_sql
    assert 'create index if not exists' in postgres_sql
