"""版本化费率的公开协作接口；提交与回滚由调用方负责。"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import ServiceException
from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_developer_api_do import AiModelRateCard
from plugins.ai.entity.vo.ai_developer_api_vo import RateCardCreateModel, RateCardModel

RATE_CARD_TYPE_CONFLICT = '模型类型与计费模式不匹配'


class RateCardService:
    """拥有计费模式、价格版本和字段映射，不返回持久化对象。"""

    def __init__(self, db: AsyncSession, *, dao: type[AiDeveloperApiDao] = AiDeveloperApiDao) -> None:
        self._db = db
        self._dao = dao

    async def list_rate_cards(self, model_id: str) -> list[RateCardModel]:
        cards = await self._dao.list_rate_cards_by_model(self._db, model_id)
        return [RateCardModel.model_validate(card) for card in cards]

    async def create_rate_card(
        self,
        model_id: str,
        model_type: str,
        payload: RateCardCreateModel,
        *,
        username: str,
        now: datetime,
    ) -> RateCardModel:
        """追加价格版本；模型存在性和模型类型由调用方从目录中确认。"""
        expected_billing_mode = 'search_unit' if model_type == 'rerank' else 'token'
        if payload.billing_mode != expected_billing_mode:
            raise ServiceException(message=RATE_CARD_TYPE_CONFLICT)
        existing = await self._dao.list_rate_cards_by_model(self._db, model_id)
        next_version = max((card.version for card in existing), default=0) + 1
        database_now = now.replace(tzinfo=None) if now.tzinfo is not None else now
        effective_from = (
            payload.effective_from.replace(tzinfo=None)
            if payload.effective_from is not None and payload.effective_from.tzinfo is not None
            else (payload.effective_from or database_now)
        )
        card = AiModelRateCard(
            rate_card_id=new_resource_id('rate'),
            platform_model_id=model_id,
            version=next_version,
            billing_mode=payload.billing_mode,
            input_price_per_token=payload.input_price_per_token,
            output_price_per_token=payload.output_price_per_token,
            cache_price_per_token=payload.cache_price_per_token,
            search_price_per_unit=payload.search_price_per_unit,
            currency=payload.currency,
            effective_from=effective_from,
            effective_to=None,
            status='active',
            created_by=username,
            created_at=database_now,
            updated_by=username,
            updated_at=database_now,
        )
        created = await self._dao.create_rate_card(self._db, card)
        return RateCardModel.model_validate(created)
