"""New memory contracts must preserve frozen model request compatibility."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest
from zhiheng.runtime.contracts import RunSnapshot


def binding(**extra) -> ModelBinding:
    return ModelBinding(
        model_id=uuid4(), connection_id=uuid4(), upstream_model='local-test', max_output_tokens=2048, **extra
    )


def test_optional_model_window_is_excluded_from_legacy_serialization() -> None:
    old = binding()
    assert 'context_window_tokens' not in old.model_dump()
    assert 'context_window_tokens' not in old.model_dump(mode='json')
    assert 'context_window_tokens' not in ModelRequest(binding=old, messages=()).model_dump()['binding']
    assert old.context_window_tokens is None


def test_model_window_serializes_only_when_configured() -> None:
    configured = binding(context_window_tokens=32768)
    assert configured.model_dump()['context_window_tokens'] == 32768
    assert ModelBinding.model_validate(configured.model_dump()) == configured


def test_model_request_stays_generic_and_forbids_chat_metadata() -> None:
    with pytest.raises(ValidationError):
        ModelRequest(binding=binding(), messages=(ModelMessage('user', 'x'),), summary_version=1)


def test_memory_tables_are_registered_through_chat_public_inventory() -> None:
    tables = schema_metadata().tables
    assert {'chat_context_states', 'chat_summary_tasks', 'chat_context_snapshots'} <= tables.keys()


def test_internal_summary_run_has_its_own_resource_type() -> None:
    from datetime import datetime, timezone

    result = RunSnapshot(
        id=uuid4(),
        resource_id=uuid4(),
        resource_type='chat_summary',
        status='pending',
        attempt_id=None,
        attempt_no=0,
        revision=1,
        projection_seq=0,
        text='',
        error=None,
        created_at=datetime.now(timezone.utc),
    )
    assert result.resource_type == 'chat_summary'


def test_memory_settings_validate_trigger_above_retention() -> None:
    from cryptography.fernet import Fernet

    from zhiheng.bootstrap.settings import Settings

    values = dict(  # noqa: C408 - readable constructor keyword fixture
        database_url='postgresql+asyncpg://local/test',
        redis_url='redis://localhost/0',
        credential_key=Fernet.generate_key().decode(),
    )
    configured = Settings(
        **values,
        chat_summary_trigger_rounds=8,
        chat_summary_keep_rounds=3,
        chat_context_input_tokens=4096,
        chat_summary_output_tokens=512,
    )
    assert configured.chat_context_input_tokens == 4096
    assert Settings(**values).chat_context_input_tokens is None
    with pytest.raises(ValidationError):
        Settings(**values, chat_summary_trigger_rounds=3, chat_summary_keep_rounds=3)
