from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec
from plugins.ai.service.application_service import ApplicationService
from plugins.ai.service.developer_key_service import DeveloperKeyService


def get_application_service(db: AsyncSession) -> ApplicationService:
    return ApplicationService(db)


def get_developer_key_service(db: AsyncSession) -> DeveloperKeyService:
    settings = LiteLLMSettings()
    codec = DeveloperKeyCodec(
        current_version=settings.api_key_pepper_current_version,
        peppers={'v1': settings.api_key_pepper_v1},
    )
    return DeveloperKeyService(db, codec)
