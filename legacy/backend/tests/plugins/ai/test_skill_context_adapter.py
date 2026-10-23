from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import SimpleNamespace

import pytest

from plugins.ai.infrastructure.skill_context_adapter import SkillContextAdapter
from plugins.ai.service.platform_chat_service import PlatformChatError


class ProviderError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class FakeProvider:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[object, int, list[str]]] = []

    async def resolve(self, db: object, user_id: int, slugs: list[str]) -> object:
        self.calls.append((db, user_id, slugs))
        if self.error is not None:
            raise self.error
        return self.result


def session_factory(session: object) -> Callable[[], AbstractAsyncContextManager[object]]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[object]:
        yield session

    return factory


@pytest.mark.asyncio
async def test_adapter_uses_independent_session_and_normalizes_snapshot() -> None:
    session = object()
    provider = FakeProvider(
        SimpleNamespace(
            system_prompt='<selected_skills />',
            snapshots=({'slug': 'deep-research', 'name': '深度研究'},),
        )
    )
    adapter = SkillContextAdapter(session_factory(session), provider)

    result = await adapter.resolve(7, ['deep-research'])

    assert provider.calls == [(session, 7, ['deep-research'])]
    assert result.snapshots == ({'slug': 'deep-research', 'name': '深度研究'},)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('provider_error', 'expected_code'),
    (
        (ProviderError('SKILL_NOT_FOUND'), 'CHAT_SKILL_NOT_FOUND'),
        (ProviderError('SKILL_DISABLED'), 'CHAT_SKILL_DISABLED'),
        (ProviderError('SKILL_SELECTION_LIMIT'), 'CHAT_SKILL_SELECTION_LIMIT'),
        (ProviderError('SKILL_CONTENT_TOO_LARGE'), 'CHAT_SKILL_CONTENT_TOO_LARGE'),
        (RuntimeError('private path'), 'CHAT_SKILL_UNAVAILABLE'),
    ),
)
async def test_adapter_maps_skill_errors_without_leaking_details(
    provider_error: Exception,
    expected_code: str,
) -> None:
    adapter = SkillContextAdapter(session_factory(object()), FakeProvider(error=provider_error))

    with pytest.raises(PlatformChatError) as raised:
        await adapter.resolve(7, ['deep-research'])

    assert raised.value.code == expected_code
    assert 'private path' not in raised.value.public_message
