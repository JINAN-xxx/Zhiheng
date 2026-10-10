from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from exceptions.exception import ServiceException
from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.do.ai_developer_api_do import AiModelRateCard
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.vo.ai_developer_api_vo import RateCardCreateModel, RateCardModel
from plugins.ai.public.rate_cards import RateCardService
from plugins.ai.service.model_catalog_service import ModelCatalogService

NOW = datetime(2026, 9, 29, 15, 30, tzinfo=timezone(timedelta(hours=8)))
DATABASE_NOW = NOW.replace(tzinfo=None)
EFFECTIVE_FROM = datetime(2026, 10, 1, 9, tzinfo=timezone(timedelta(hours=-4)))
NEXT_RATE_VERSION = 8


class CountingSession(AsyncSession):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError('commit failed')
        await super().commit()

    async def rollback(self) -> None:
        self.rollbacks += 1
        await super().rollback()


@pytest_asyncio.fixture
async def rate_db(tmp_path: Path) -> AsyncIterator[async_sessionmaker[CountingSession]]:
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "rates.db"}')

    @event.listens_for(engine.sync_engine, 'connect')
    def enable_foreign_keys(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    maker = async_sessionmaker(engine, class_=CountingSession, expire_on_commit=True)
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiModelRateCard.__table__.create)
    async with maker() as db:
        db.add_all([_model(model_type, model_type=model_type) for model_type in ('chat', 'embedding', 'rerank')])
        await db.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


def _model(model_id: str, *, model_type: str = 'chat') -> AiModelCatalog:
    return AiModelCatalog(
        model_id=model_id,
        model_code=f'code-{model_id}',
        display_name=model_id,
        model_type=model_type,
        lifecycle_status='draft',
    )


def _token_payload(**values: Any) -> RateCardCreateModel:
    return RateCardCreateModel(
        inputPricePerToken=Decimal('0.000001234567'),
        outputPricePerToken=Decimal('0.000008765432'),
        cachePricePerToken=Decimal('0.000000123456'),
        currency='CNY',
        **values,
    )


def _existing_card(model_id: str, version: int) -> AiModelRateCard:
    return AiModelRateCard(
        rate_card_id=f'rate-{model_id}-{version}',
        platform_model_id=model_id,
        version=version,
        billing_mode='token',
        input_price_per_token=Decimal('0.000002'),
        output_price_per_token=Decimal('0.000003'),
        cache_price_per_token=None,
        search_price_per_unit=None,
        currency='USD',
        effective_from=DATABASE_NOW,
        effective_to=None,
        status='active',
        created_by='original',
        created_at=DATABASE_NOW,
        updated_by='original',
        updated_at=DATABASE_NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('model_type', ['chat', 'embedding', 'rerank'])
@pytest.mark.parametrize('effective_from', [None, EFFECTIVE_FROM, EFFECTIVE_FROM.replace(tzinfo=None)])
async def test_rate_service_maps_prices_timestamps_and_audit_fields(
    rate_db: async_sessionmaker[CountingSession], model_type: str, effective_from: datetime | None
) -> None:
    payload = (
        RateCardCreateModel(
            billingMode='search_unit',
            searchPricePerUnit=Decimal('0.001234567891'),
            currency='CNY',
            effectiveFrom=effective_from,
        )
        if model_type == 'rerank'
        else _token_payload(effectiveFrom=effective_from)
    )
    async with rate_db() as db:
        service = RateCardService(db)
        result = await service.create_rate_card(model_type, model_type, payload, username='operator', now=NOW)
        assert isinstance(result, RateCardModel)
        assert result.version == 1
        assert result.billing_mode == payload.billing_mode
        assert result.input_price_per_token == payload.input_price_per_token
        assert result.output_price_per_token == payload.output_price_per_token
        assert result.cache_price_per_token == payload.cache_price_per_token
        assert result.search_price_per_unit == payload.search_price_per_unit
        assert result.currency == 'CNY'
        assert result.effective_from == (effective_from or NOW).replace(tzinfo=None)
        assert result.effective_to is None
        assert result.status == 'active'
        assert result.rate_card_id.startswith('rate_')
        record = await db.get(AiModelRateCard, result.rate_card_id)
        assert record.platform_model_id == model_type
        assert record.created_by == record.updated_by == 'operator'
        assert record.created_at == record.updated_at == DATABASE_NOW
        assert await service.list_rate_cards(model_type) == [result]
        assert db.commits == db.rollbacks == 0
        await db.commit()
    # The public result is independent of expired or detached ORM instances.
    assert result.model_dump(by_alias=True, mode='json')['effectiveFrom'] == result.effective_from.isoformat()


@pytest.mark.asyncio
async def test_rate_service_appends_max_version_without_altering_existing_cards(
    rate_db: async_sessionmaker[CountingSession],
) -> None:
    async with rate_db() as db:
        earlier = _existing_card('chat', 2)
        earlier.status = 'disabled'
        earlier.effective_to = DATABASE_NOW
        db.add_all([earlier, _existing_card('chat', 7), _existing_card('embedding', 99)])
        await db.commit()
        service = RateCardService(db)
        before = await service.list_rate_cards('chat')
        new_card = await service.create_rate_card('chat', 'chat', _token_payload(), username='admin', now=DATABASE_NOW)
        assert new_card.version == NEXT_RATE_VERSION
        assert await service.list_rate_cards('chat') == [new_card, *before]
        assert [card.version for card in await service.list_rate_cards('embedding')] == [99]
        assert await service.list_rate_cards('unpriced') == []
        await db.commit()
    async with rate_db() as db:
        assert await RateCardService(db).list_rate_cards('chat') == [new_card, *before]


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['list', 'create'])
@pytest.mark.parametrize('finish', ['commit', 'rollback'])
async def test_rate_service_leaves_pending_writes_and_transaction_to_caller(
    rate_db: async_sessionmaker[CountingSession], operation: str, finish: str
) -> None:
    async with rate_db() as db:
        db.add(_model('pending'))
        await db.flush()
        service = RateCardService(db)
        if operation == 'list':
            assert await service.list_rate_cards('chat') == []
        else:
            await service.create_rate_card('chat', 'chat', _token_payload(), username='admin', now=NOW)
        assert db.in_transaction()
        assert db.commits == db.rollbacks == 0
        await getattr(db, finish)()
    async with rate_db() as db:
        assert (await db.get(AiModelCatalog, 'pending') is not None) is (finish == 'commit')
        cards = list((await db.scalars(select(AiModelRateCard))).all())
        assert len(cards) == int(operation == 'create' and finish == 'commit')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('model_type', 'billing_mode'),
    [('chat', 'search_unit'), ('embedding', 'search_unit'), ('rerank', 'token')],
)
async def test_rate_service_rejects_incompatible_billing_without_ending_transaction(
    rate_db: async_sessionmaker[CountingSession], model_type: str, billing_mode: str
) -> None:
    payload = RateCardCreateModel(
        billingMode=billing_mode,
        searchPricePerUnit=Decimal('0.001') if billing_mode == 'search_unit' else None,
    )
    async with rate_db() as db:
        db.add(_model('pending'))
        await db.flush()
        with pytest.raises(ServiceException) as raised:
            await RateCardService(db).create_rate_card(model_type, model_type, payload, username='admin', now=NOW)
        assert raised.value.message == '模型类型与计费模式不匹配'
        assert db.commits == db.rollbacks == 0
        assert db.in_transaction()
        await db.commit()
    async with rate_db() as db:
        assert await db.get(AiModelCatalog, 'pending') is not None
        assert list((await db.scalars(select(AiModelRateCard))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model_type', ['chat', 'embedding', 'rerank'])
async def test_catalog_rate_operations_keep_public_dto_and_commit_once(
    rate_db: async_sessionmaker[CountingSession], model_type: str
) -> None:
    payload = (
        RateCardCreateModel(billingMode='search_unit', searchPricePerUnit=Decimal('0.004'))
        if model_type == 'rerank'
        else _token_payload()
    )
    async with rate_db() as db:
        service = ModelCatalogService(db, runtime=None)
        result = await service.create_rate_card(model_type, payload, username='operator', now=NOW)
        assert db.commits == 1
        assert db.rollbacks == 0
        assert not db.in_transaction()
        assert result.billing_mode == payload.billing_mode
        assert result.input_price_per_token == payload.input_price_per_token
        assert result.search_price_per_unit == payload.search_price_per_unit
    async with rate_db() as db:
        cards = await ModelCatalogService(db, runtime=None).list_rate_cards(model_type)
        assert cards == [result]
        assert db.commits == db.rollbacks == 0
    assert result.model_dump(by_alias=True, mode='json')['billingMode'] == payload.billing_mode


@pytest.mark.asyncio
async def test_catalog_missing_model_does_not_list_rate_cards(rate_db: async_sessionmaker[CountingSession]) -> None:
    async with rate_db() as db:
        with pytest.raises(ModelServiceError) as raised:
            await ModelCatalogService(db, runtime=None).list_rate_cards('missing')
        assert raised.value.code == 'MODEL_NOT_FOUND'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'billing_mode', 'commit'])
async def test_catalog_rolls_back_entire_rate_creation_on_failure(
    rate_db: async_sessionmaker[CountingSession], failure: str
) -> None:
    async with rate_db() as db:
        db.add(_model('pending'))
        await db.flush()
        db.fail_commit = failure == 'commit'
        model_id = 'missing' if failure == 'missing' else 'rerank' if failure == 'billing_mode' else 'chat'
        error = (
            ModelServiceError
            if failure == 'missing'
            else ServiceException
            if failure == 'billing_mode'
            else RuntimeError
        )
        with pytest.raises(error) as raised:
            await ModelCatalogService(db, runtime=None).create_rate_card(
                model_id, _token_payload(), username='operator', now=NOW
            )
        if failure == 'missing':
            assert raised.value.code == 'MODEL_NOT_FOUND'
        elif failure == 'billing_mode':
            assert raised.value.message == '模型类型与计费模式不匹配'
        else:
            assert str(raised.value) == 'commit failed'
        assert db.rollbacks == 1
        assert db.commits == int(failure == 'commit')
        assert not db.in_transaction()
    async with rate_db() as db:
        assert await db.get(AiModelCatalog, 'pending') is None
        assert list((await db.scalars(select(AiModelRateCard))).all()) == []


class FailingRateReadDao(AiDeveloperApiDao):
    @classmethod
    async def list_rate_cards_by_model(cls, db: AsyncSession, model_id: str) -> list[AiModelRateCard]:
        await super().list_rate_cards_by_model(db, model_id)
        raise RuntimeError('rate lookup failed')


@pytest.mark.asyncio
async def test_catalog_rolls_back_rate_lookup_failure(rate_db: async_sessionmaker[CountingSession]) -> None:
    async with rate_db() as db:
        db.add(_model('pending'))
        await db.flush()
        service = ModelCatalogService(db, runtime=None, rate_cards=RateCardService(db, dao=FailingRateReadDao))
        with pytest.raises(RuntimeError, match='rate lookup failed'):
            await service.create_rate_card('chat', _token_payload(), username='operator', now=NOW)
        assert db.commits == 0
        assert db.rollbacks == 1
        assert not db.in_transaction()
    async with rate_db() as db:
        assert await db.get(AiModelCatalog, 'pending') is None
        assert list((await db.scalars(select(AiModelRateCard))).all()) == []
