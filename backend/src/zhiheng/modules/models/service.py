from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.contracts import ConnectionInfo, ModelBinding, ModelInfo, ModelProvider
from zhiheng.modules.models.orm import ModelConnection, ModelDefinition
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class ModelService:
    def __init__(self, cipher: CredentialCipher, timeout_seconds: float) -> None:
        self._cipher = cipher
        self._timeout = timeout_seconds

    async def create_connection(
        self, session: AsyncSession, principal: Principal, *, name: str, base_url: str, api_key: str
    ) -> ConnectionInfo:
        principal.require_admin()
        connection = ModelConnection(
            name=name, base_url=base_url.rstrip('/'), encrypted_api_key=self._cipher.encrypt(api_key)
        )
        session.add(connection)
        await session.flush()
        return self._connection_info(connection)

    async def list_connections(self, session: AsyncSession, principal: Principal) -> list[ConnectionInfo]:
        principal.require_admin()
        rows = await session.scalars(select(ModelConnection).order_by(ModelConnection.name).limit(100))
        return [self._connection_info(row) for row in rows]

    async def create_model(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        name: str,
        connection_id: UUID,
        upstream_model: str,
        max_output_tokens: int,
    ) -> ModelInfo:
        principal.require_admin()
        if await session.get(ModelConnection, connection_id) is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        model = ModelDefinition(
            name=name,
            connection_id=connection_id,
            upstream_model=upstream_model,
            max_output_tokens=max_output_tokens,
        )
        session.add(model)
        await session.flush()
        return self._model_info(model)

    async def list_models(self, session: AsyncSession) -> list[ModelInfo]:
        rows = await session.scalars(select(ModelDefinition).where(ModelDefinition.enabled.is_(True)).limit(100))
        return [self._model_info(row) for row in rows]

    async def resolve(self, session: AsyncSession, model_id: UUID) -> ModelBinding:
        model = await session.get(ModelDefinition, model_id)
        if model is None or not model.enabled:
            raise AppError('model_unavailable', '模型不存在或不可用', 404)
        return ModelBinding(
            model_id=model.id,
            connection_id=model.connection_id,
            upstream_model=model.upstream_model,
            max_output_tokens=model.max_output_tokens,
        )

    async def prepare_provider(self, session: AsyncSession, binding: ModelBinding) -> ModelProvider:
        """Composition only: credentials remain within the model connection owner."""
        connection = await session.get(ModelConnection, binding.connection_id)
        if connection is None:
            raise AppError('connection_unavailable', '模型连接不可用', 503)
        return OpenAIChatProvider(
            base_url=connection.base_url,
            api_key=self._cipher.decrypt(connection.encrypted_api_key),
            model=binding.upstream_model,
            max_output_tokens=binding.max_output_tokens,
            timeout_seconds=self._timeout,
        )

    @staticmethod
    def _connection_info(row: ModelConnection) -> ConnectionInfo:
        return ConnectionInfo(id=row.id, name=row.name, base_url=row.base_url)

    @staticmethod
    def _model_info(row: ModelDefinition) -> ModelInfo:
        return ModelInfo(
            id=row.id,
            name=row.name,
            connection_id=row.connection_id,
            upstream_model=row.upstream_model,
            max_output_tokens=row.max_output_tokens,
            enabled=row.enabled,
        )
