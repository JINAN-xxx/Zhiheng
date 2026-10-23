from redis.asyncio import Redis

from zhiheng.application.chat_runs import ChatRunService
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.infrastructure.database import Database
from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.developer.public import UsageService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.models.public import ModelBinding, ModelProvider, ModelService
from zhiheng.runtime.outbox import OutboxStore
from zhiheng.runtime.runs import RunStore


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_url.get_secret_value())
        self.redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
        self.cipher = CredentialCipher(settings.credential_key.get_secret_value())
        self.identity = IdentityService(settings.session_ttl_seconds)
        self.models = ModelService(self.cipher, settings.provider_timeout_seconds)
        self.usage = UsageService()
        self.runs = RunStore()
        self.chat = ChatService()
        self.admission = ChatRunService(self.chat, self.models, self.usage, self.runs)
        self.outbox = OutboxStore()

    async def prepare_provider(self, binding: ModelBinding) -> ModelProvider:
        async with self.database.sessions() as session:
            return await self.models.prepare_provider(session, binding)

    async def close(self) -> None:
        try:
            await self.redis.aclose()
        finally:
            await self.database.close()
