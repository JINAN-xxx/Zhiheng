import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from zhiheng.bootstrap.settings import Settings


def test_rerank_batch_budget_is_an_explicit_setting_without_a_lifetime_quota() -> None:
    values = {
        'database_url': 'postgresql+asyncpg://user:test@localhost/test',
        'redis_url': 'redis://localhost/0',
        'credential_key': Fernet.generate_key().decode(),
    }
    settings = Settings(**values)
    assert settings.knowledge_rerank_batch_tokens == 48000
    assert 'token_limit' not in Settings.model_fields
    assert Settings(**values, knowledge_rerank_batch_tokens=1).knowledge_rerank_batch_tokens == 1
    for value in [0, 48001]:
        with pytest.raises(ValidationError):
            Settings(**values, knowledge_rerank_batch_tokens=value)
