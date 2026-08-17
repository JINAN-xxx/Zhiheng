from datetime import datetime

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.dao.platform_chat_dao import PlatformChatDao
from plugins.ai.entity.do.ai_chat_platform_do import AiChatPreferences
from plugins.ai.entity.vo.ai_platform_chat_vo import PlatformChatPreferenceModel
from plugins.ai.service.chat_preferences_service import ChatPreferencesService

NOW = datetime(2026, 8, 17, 12, 0, 0)
CUSTOM_TEMPERATURE = 0.4
REPLACEMENT_HISTORY_COUNT = 12


@pytest_asyncio.fixture
async def session_maker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(AiChatPreferences.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=True)
    try:
        yield maker
    finally:
        await engine.dispose()


def test_platform_chat_preferences_have_safe_defaults_and_strict_camel_case() -> None:
    preferences = PlatformChatPreferenceModel()

    assert preferences.model_dump(by_alias=True) == {
        'temperature': None,
        'addHistory': True,
        'historyMessageCount': 20,
        'systemPrompt': None,
        'metricsVisible': True,
        'visionEnabled': True,
        'imageMaxSizeMb': 10,
    }
    with pytest.raises(ValidationError):
        PlatformChatPreferenceModel(add_history=False)
    with pytest.raises(ValidationError):
        PlatformChatPreferenceModel(apiKey='must-not-be-accepted')

    private_prompt = PlatformChatPreferenceModel(systemPrompt='PRIVATE-SYSTEM-PROMPT')
    assert 'PRIVATE-SYSTEM-PROMPT' not in repr(private_prompt)
    assert private_prompt.model_dump(by_alias=True)['systemPrompt'] == 'PRIVATE-SYSTEM-PROMPT'


@pytest.mark.parametrize(
    'payload',
    [
        {'temperature': -0.1},
        {'temperature': 2.1},
        {'historyMessageCount': -1},
        {'historyMessageCount': 101},
        {'systemPrompt': 'x' * 10001},
        {'imageMaxSizeMb': 0},
        {'imageMaxSizeMb': 51},
        {'addHistory': 1},
    ],
)
def test_platform_chat_preferences_reject_invalid_ranges_and_coercion(payload: dict) -> None:
    with pytest.raises(ValidationError):
        PlatformChatPreferenceModel.model_validate(payload)


@pytest.mark.asyncio
async def test_preferences_service_returns_defaults_without_creating_a_row(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        service = ChatPreferencesService(session)

        result = await service.get_preferences(user_id=7)
        stored = await PlatformChatDao.get_preferences(session, user_id=7)

    assert result == PlatformChatPreferenceModel()
    assert stored is None


@pytest.mark.asyncio
async def test_preferences_service_creates_and_replaces_preferences_with_expiring_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        service = ChatPreferencesService(session)
        created = await service.update_preferences(
            user_id=7,
            preferences=PlatformChatPreferenceModel(
                temperature=CUSTOM_TEMPERATURE,
                addHistory=False,
                historyMessageCount=8,
                systemPrompt='只回答平台问题',
                metricsVisible=False,
                visionEnabled=False,
                imageMaxSizeMb=5,
            ),
            now=NOW,
        )
        replaced = await service.update_preferences(
            user_id=7,
            preferences=PlatformChatPreferenceModel(
                temperature=None,
                addHistory=True,
                historyMessageCount=REPLACEMENT_HISTORY_COUNT,
                systemPrompt=None,
                metricsVisible=True,
                visionEnabled=True,
                imageMaxSizeMb=10,
            ),
            now=NOW,
        )

    assert created.temperature == CUSTOM_TEMPERATURE
    assert created.system_prompt == '只回答平台问题'
    assert replaced == PlatformChatPreferenceModel(historyMessageCount=REPLACEMENT_HISTORY_COUNT)

    async with session_maker() as verification_session:
        stored = await PlatformChatDao.get_preferences(verification_session, user_id=7)
        assert stored is not None
        assert stored.temperature is None
        assert stored.system_prompt is None
        assert stored.history_message_count == REPLACEMENT_HISTORY_COUNT


class FailingCommitSession:
    def __init__(self) -> None:
        self.rollback_count = 0

    async def commit(self) -> None:
        raise RuntimeError('PRIVATE-DB-ERROR')

    async def rollback(self) -> None:
        self.rollback_count += 1


class FakePreferencesDao:
    @staticmethod
    async def get_preferences(_db: object, _user_id: int) -> None:
        return None

    @staticmethod
    async def create_preferences(_db: object, preferences: AiChatPreferences) -> AiChatPreferences:
        return preferences


@pytest.mark.asyncio
async def test_preferences_service_rolls_back_and_preserves_commit_error() -> None:
    session = FailingCommitSession()
    service = ChatPreferencesService(session, preferences_dao=FakePreferencesDao)

    with pytest.raises(RuntimeError, match='PRIVATE-DB-ERROR'):
        await service.update_preferences(
            user_id=7,
            preferences=PlatformChatPreferenceModel(),
            now=NOW,
        )

    assert session.rollback_count == 1
