"""Validate a frozen provider configuration outside SQL transactions before committing it."""

import asyncio
from http import HTTPStatus
from uuid import UUID

from zhiheng.infrastructure.database import Database
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.models.public import ModelInfo, ModelMessage, ModelSaveCheck, ModelService, ProviderError
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

PROBE_OUTPUT_TOKENS = 512
PROBE_TIMEOUT_SECONDS = 15


class ModelConfigurationService:
    def __init__(self, database: Database, models: ModelService, identity: IdentityService) -> None:
        self.database, self.models, self.identity = database, models, identity

    async def save(self, principal: Principal, fields: dict[str, object], model_id: UUID | None = None) -> ModelInfo:
        async with self.database.sessions() as session:
            check = await self.models.configuration.prepare(session, principal, fields, model_id)
        upstream_model, used_trial = await self._validate(principal.id, check)
        async with self.database.sessions.begin() as session:
            current = await self.identity.principal_for_user(session, principal.id)
            if used_trial:
                current.require_permission('platform:models:test')
            return await self.models.configuration.save_verified(session, current, check, upstream_model=upstream_model)

    async def _validate(self, actor_id: UUID, check: ModelSaveCheck) -> tuple[str | None, bool]:
        if check.provider is None:
            return None, False
        try:
            return await check.provider.validate(), False
        except AppError as missing:
            if missing.code != 'model_validation_not_found' or check.binding.model_type != 'chat':
                raise
            try:
                await self._probe_chat(actor_id, check)
            except ProviderError as error:
                if error.upstream_status == HTTPStatus.NOT_FOUND:
                    raise missing from None
                if error.upstream_status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
                    raise AppError('model_validation_auth', '服务商拒绝试调用，请检查密钥和模型权限', 422) from None
                if error.reason == 'insufficient_balance':
                    raise AppError(error.code, error.message, error.status_code) from None
                if error.upstream_status == HTTPStatus.TOO_MANY_REQUESTS:
                    raise AppError('model_validation_rate_limited', '模型试调用被限流，请稍后重试', 429) from None
                if error.reason in {'timeout', 'connect_timeout'}:
                    raise AppError('model_validation_timeout', '模型试调用超时，请稍后重试', 504) from None
                raise AppError('model_validation_probe_failed', '模型试调用失败，请核对上游模型标识', 502) from None
        return check.configuration.upstream_model, True

    async def _probe_chat(self, actor_id: UUID, check: ModelSaveCheck) -> None:
        binding = check.binding.model_copy(
            update={'max_output_tokens': min(check.binding.max_output_tokens, PROBE_OUTPUT_TOKENS)}
        )
        async with self.database.sessions() as session:
            current = await self.identity.principal_for_user(session, actor_id)
            for action in check.required_actions:
                current.require_permission(f'platform:models:{action}')
            if not current.has_any_permission('platform:models:test'):
                raise AppError('forbidden', '服务商目录未找到该模型，继续验证需要模型试用与连接测试权限', 403)
            provider = await self.models.bindings.provider(session, binding)

        async def completed() -> bool:
            has_text = finished = False
            async for delta in provider.stream((ModelMessage('user', 'Reply with OK only.'),)):
                has_text |= bool(delta.text.strip())
                finished |= delta.finish_reason in {'stop', 'length'}
            return has_text and finished

        try:
            valid = await asyncio.wait_for(completed(), timeout=PROBE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as error:
            raise AppError('model_validation_timeout', '模型试调用超时，请稍后重试', 504) from error
        if not valid:
            raise AppError('model_validation_probe_failed', '模型试调用未返回有效回复，无法确认模型可用', 502)
