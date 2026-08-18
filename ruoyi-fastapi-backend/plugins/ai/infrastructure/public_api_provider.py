from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperKeyAuthenticator
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec
from plugins.ai.infrastructure.model_runtime_provider import get_model_runtime_engine
from plugins.ai.service.api_entitlement_service import ApiEntitlementService
from plugins.ai.service.model_service import ModelService
from plugins.ai.service.openai_compat_service import OpenAICompatService


@dataclass(frozen=True, slots=True)
class PublicApiServices:
    authenticator: DeveloperKeyAuthenticator
    entitlement: ApiEntitlementService
    compat: OpenAICompatService


def get_public_api_services(
    request: Request,
    db: AsyncSession,
    settings: LiteLLMSettings,
) -> PublicApiServices:
    """Build request-scoped public services without taking ownership of shared Runtime."""
    codec = DeveloperKeyCodec(
        current_version=settings.api_key_pepper_current_version,
        peppers={'v1': settings.api_key_pepper_v1},
    )
    authenticator = DeveloperKeyAuthenticator(
        db,
        codec,
        configured=settings.developer_api_configured,
    )
    entitlement = ApiEntitlementService(db)
    model_service = ModelService(db, get_model_runtime_engine(request))
    return PublicApiServices(
        authenticator=authenticator,
        entitlement=entitlement,
        compat=OpenAICompatService(entitlement, model_service),
    )
