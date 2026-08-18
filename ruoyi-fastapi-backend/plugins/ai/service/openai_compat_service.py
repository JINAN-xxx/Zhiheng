import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Protocol, TypeVar, cast
from uuid import uuid4

from plugins.ai.domain.model_runtime import ModelMessage
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_openai_compat_vo import (
    OpenAIChatCompletionRequestModel,
    OpenAIChatCompletionResponseModel,
)
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.infrastructure.openai_stream_adapter import OpenAIStreamAdapter
from plugins.ai.service.api_entitlement_service import ApiEntitlementService, AuthorizedPublicModel
from plugins.ai.service.model_service import ModelService, ModelServiceChatResult, ModelServiceStreamResult

ResultT = TypeVar('ResultT')
_COMPLETION_ID_PATTERN = re.compile(r'^chatcmpl_[0-9a-f]{32}$')


class EntitlementResolver(Protocol):
    async def resolve_model(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
    ) -> AuthorizedPublicModel: ...


class PublicModelCaller(Protocol):
    async def chat(
        self,
        model_id: str,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceChatResult: ...

    async def stream_chat(
        self,
        model_id: str,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceStreamResult: ...


@dataclass(frozen=True, slots=True)
class _CompatOutcome(Generic[ResultT]):
    value: ResultT | None = None
    error: PublicApiError | None = None
    cancelled: bool = False


def _unwrap_outcome(outcome: _CompatOutcome[ResultT]) -> ResultT:
    if outcome.cancelled:
        raise asyncio.CancelledError from None
    if outcome.error is not None:
        raise outcome.error from None
    return cast('ResultT', outcome.value)


class OpenAICompatService:
    def __init__(
        self,
        entitlement: EntitlementResolver | ApiEntitlementService,
        model_service: PublicModelCaller | ModelService,
        *,
        completion_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._entitlement = entitlement
        self._model_service = model_service
        self._completion_id_factory = completion_id_factory or (lambda: f'chatcmpl_{uuid4().hex}')
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def complete(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIChatCompletionRequestModel,
    ) -> OpenAIChatCompletionResponseModel:
        operation = self._complete_sensitive
        outcome = await operation(identity, request)
        del self, identity, request, operation
        return _unwrap_outcome(outcome)

    async def stream(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIChatCompletionRequestModel,
    ) -> OpenAIStreamAdapter:
        operation = self._stream_sensitive
        outcome = await operation(identity, request)
        del self, identity, request, operation
        return _unwrap_outcome(outcome)

    async def _complete_sensitive(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIChatCompletionRequestModel,
    ) -> _CompatOutcome[OpenAIChatCompletionResponseModel]:
        try:
            if request.stream:
                return _CompatOutcome(error=PublicApiError.invalid_request())
            resolved = await self._entitlement.resolve_model(identity, request.model)
            completion_id, created = self._completion_snapshot()
            result = await self._model_service.chat(
                resolved.platform_model_id,
                self._messages(request),
                self._parameters(request),
            )
            response = self._response(completion_id, created, request.model, result)
            return _CompatOutcome(value=response)
        except asyncio.CancelledError:
            return _CompatOutcome(cancelled=True)
        except PublicApiError as error:
            return _CompatOutcome(error=self._fresh_public_error(error))
        except ModelServiceError as error:
            return _CompatOutcome(error=self._map_model_error(error))
        except Exception:
            return _CompatOutcome(error=PublicApiError.service_unavailable())

    async def _stream_sensitive(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIChatCompletionRequestModel,
    ) -> _CompatOutcome[OpenAIStreamAdapter]:
        try:
            if not request.stream:
                return _CompatOutcome(error=PublicApiError.invalid_request())
            resolved = await self._entitlement.resolve_model(identity, request.model)
            completion_id, created = self._completion_snapshot()
            stream = await self._model_service.stream_chat(
                resolved.platform_model_id,
                self._messages(request),
                self._parameters(request),
            )
            return _CompatOutcome(
                value=OpenAIStreamAdapter(
                    completion_id=completion_id,
                    created=created,
                    model_code=request.model,
                    stream=stream,
                )
            )
        except asyncio.CancelledError:
            return _CompatOutcome(cancelled=True)
        except PublicApiError as error:
            return _CompatOutcome(error=self._fresh_public_error(error))
        except ModelServiceError as error:
            return _CompatOutcome(error=self._map_model_error(error))
        except Exception:
            return _CompatOutcome(error=PublicApiError.service_unavailable())

    def _completion_snapshot(self) -> tuple[str, int]:
        value = self._clock()
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        completion_id = self._completion_id_factory()
        created = int(normalized.timestamp())
        if not isinstance(completion_id, str) or _COMPLETION_ID_PATTERN.fullmatch(completion_id) is None or created < 0:
            raise ValueError('Invalid completion snapshot')
        return completion_id, created

    @staticmethod
    def _messages(request: OpenAIChatCompletionRequestModel) -> tuple[ModelMessage, ...]:
        return tuple(ModelMessage(role=message.role, content=message.content) for message in request.messages)

    @staticmethod
    def _parameters(request: OpenAIChatCompletionRequestModel) -> dict[str, object]:
        values: dict[str, object] = {}
        for name in (
            'temperature',
            'top_p',
            'max_tokens',
            'presence_penalty',
            'frequency_penalty',
            'stop',
            'user',
        ):
            value = getattr(request, name)
            if value is not None:
                values[name] = list(value) if name == 'stop' and isinstance(value, list) else value
        return values

    @staticmethod
    def _response(
        completion_id: str,
        created: int,
        model_code: str,
        result: ModelServiceChatResult,
    ) -> OpenAIChatCompletionResponseModel:
        usage = result.response.usage
        return OpenAIChatCompletionResponseModel.model_validate(
            {
                'id': completion_id,
                'object': 'chat.completion',
                'created': created,
                'model': model_code,
                'choices': [
                    {
                        'index': 0,
                        'message': {'role': 'assistant', 'content': result.response.text},
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {
                    'prompt_tokens': usage.input_tokens,
                    'completion_tokens': usage.output_tokens,
                    'total_tokens': usage.input_tokens + usage.output_tokens,
                },
            }
        )

    @staticmethod
    def _map_model_error(error: ModelServiceError) -> PublicApiError:
        if error.code in {'MODEL_NOT_FOUND', 'MODEL_DISABLED'}:
            return PublicApiError.model_not_found()
        if error.code in {
            'MODEL_BINDING_MISSING',
            'MODEL_RUNTIME_MISCONFIGURED',
            'MODEL_RUNTIME_UNAVAILABLE',
            'MODEL_RUNTIME_TIMEOUT',
        }:
            return PublicApiError.service_unavailable()
        return PublicApiError.upstream_error()

    @staticmethod
    def _fresh_public_error(error: PublicApiError) -> PublicApiError:
        factories = {
            'not_found': PublicApiError.feature_disabled,
            'invalid_api_key': PublicApiError.invalid_api_key,
            'permission_denied': PublicApiError.permission_denied,
            'model_not_found': PublicApiError.model_not_found,
            'invalid_request': PublicApiError.invalid_request,
            'upstream_error': PublicApiError.upstream_error,
            'service_unavailable': PublicApiError.service_unavailable,
        }
        return factories[error.code]()
