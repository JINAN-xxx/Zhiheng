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
    OpenAIEmbeddingRequestModel,
    OpenAIEmbeddingResponseModel,
    OpenAIRerankRequestModel,
    OpenAIRerankResponseModel,
)
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.infrastructure.openai_stream_adapter import OpenAIStreamAdapter
from plugins.ai.service.model_service import (
    ModelService,
    ModelServiceChatResult,
    ModelServiceEmbeddingResult,
    ModelServiceRerankResult,
    ModelServiceStreamResult,
)
from plugins.ai.service.public_invocation_coordinator import (
    InvocationSnapshotRecord,
    OperationResult,
    PublicInvocationCoordinator,
    RuntimeUsage,
)

ResultT = TypeVar('ResultT')
_COMPLETION_ID_PATTERN = re.compile(r'^chatcmpl_[0-9a-f]{32}$')


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
    """OpenAI 兼容公开 API：统一经过 PublicInvocationCoordinator 的授权、配额与 Ledger。"""

    def __init__(
        self,
        coordinator: PublicInvocationCoordinator,
        model_service: PublicModelCaller | ModelService,
        *,
        completion_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._coordinator = coordinator
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

    async def embed(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIEmbeddingRequestModel,
    ) -> OpenAIEmbeddingResponseModel:
        operation = self._embed_sensitive
        outcome = await operation(identity, request)
        del self, identity, request, operation
        return _unwrap_outcome(outcome)

    async def rerank(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIRerankRequestModel,
    ) -> OpenAIRerankResponseModel:
        operation = self._rerank_sensitive
        outcome = await operation(identity, request)
        del self, identity, request, operation
        return _unwrap_outcome(outcome)

    async def _embed_sensitive(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIEmbeddingRequestModel,
    ) -> _CompatOutcome[OpenAIEmbeddingResponseModel]:
        try:
            inputs = self._embedding_inputs(request)
            result = await self._coordinator.run(
                identity,
                request.model,
                'embedding',
                operation_type='embedding',
                input_bytes=sum(len(item.encode('utf-8')) for item in inputs),
                operation=lambda snapshot: self._call_embed(snapshot, request, inputs),
            )
            return _CompatOutcome(value=self._embedding_response(request.model, result))
        except asyncio.CancelledError:
            return _CompatOutcome(cancelled=True)
        except PublicApiError as error:
            return _CompatOutcome(error=self._fresh_public_error(error))
        except ModelServiceError as error:
            return _CompatOutcome(error=self._map_model_error(error))
        except Exception:
            return _CompatOutcome(error=PublicApiError.service_unavailable())

    async def _rerank_sensitive(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIRerankRequestModel,
    ) -> _CompatOutcome[OpenAIRerankResponseModel]:
        try:
            documents = self._rerank_documents(request)
            result = await self._coordinator.run(
                identity,
                request.model,
                'rerank',
                operation_type='rerank',
                input_bytes=len(request.query.encode('utf-8')) + sum(len(doc.encode('utf-8')) for doc in documents),
                operation=lambda snapshot: self._call_rerank(snapshot, request, documents),
            )
            return _CompatOutcome(value=self._rerank_response(request, result, documents))
        except asyncio.CancelledError:
            return _CompatOutcome(cancelled=True)
        except PublicApiError as error:
            return _CompatOutcome(error=self._fresh_public_error(error))
        except ModelServiceError as error:
            return _CompatOutcome(error=self._map_model_error(error))
        except Exception:
            return _CompatOutcome(error=PublicApiError.service_unavailable())

    async def _complete_sensitive(
        self,
        identity: DeveloperApiIdentity,
        request: OpenAIChatCompletionRequestModel,
    ) -> _CompatOutcome[OpenAIChatCompletionResponseModel]:
        try:
            if request.stream:
                return _CompatOutcome(error=PublicApiError.invalid_request())
            completion_id, created = self._completion_snapshot()
            result = await self._coordinator.run(
                identity,
                request.model,
                'chat',
                operation_type='chat',
                input_bytes=self._input_bytes(request),
                output_ceiling=request.max_tokens,
                operation=lambda snapshot: self._call_chat(snapshot, request),
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
            completion_id, created = self._completion_snapshot()
            stream = await self._coordinator.run_stream(
                identity,
                request.model,
                'chat',
                operation_type='chat',
                input_bytes=self._input_bytes(request),
                output_ceiling=request.max_tokens,
                operation=lambda snapshot: self._model_service.stream_chat(
                    snapshot.platform_model_id,
                    self._messages(request),
                    self._parameters(request),
                ),
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

    async def _call_chat(
        self,
        snapshot: InvocationSnapshotRecord,
        request: OpenAIChatCompletionRequestModel,
    ) -> OperationResult:
        result = await self._model_service.chat(
            snapshot.platform_model_id,
            self._messages(request),
            self._parameters(request),
        )
        usage = result.response.usage
        return OperationResult(
            usage=RuntimeUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_tokens=usage.cache_read_tokens + usage.cache_write_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                raw_cost=usage.raw_cost,
                upstream_request_id=result.response.request_id,
            ),
            value=result,
        )

    async def _call_embed(
        self,
        snapshot: InvocationSnapshotRecord,
        request: OpenAIEmbeddingRequestModel,
        inputs: tuple[str, ...],
    ) -> OperationResult:
        parameters: dict[str, object] = {}
        if request.dimensions is not None:
            parameters['dimensions'] = request.dimensions
        if request.user is not None:
            parameters['user'] = request.user
        result = await self._model_service.embed(snapshot.platform_model_id, inputs, parameters)
        return OperationResult(
            usage=RuntimeUsage(
                input_tokens=result.response.input_tokens,
                raw_cost=result.response.raw_cost,
            ),
            value=result,
        )

    async def _call_rerank(
        self,
        snapshot: InvocationSnapshotRecord,
        request: OpenAIRerankRequestModel,
        documents: tuple[str, ...],
    ) -> OperationResult:
        parameters: dict[str, object] = {}
        if request.top_n is not None:
            parameters['top_n'] = request.top_n
        if request.return_documents is not None:
            parameters['return_documents'] = request.return_documents
        result = await self._model_service.rerank(snapshot.platform_model_id, request.query, documents, parameters)
        return OperationResult(
            usage=RuntimeUsage(
                input_tokens=result.response.input_tokens,
                search_units=result.response.search_units,
                raw_cost=result.response.raw_cost,
            ),
            value=result,
        )

    @staticmethod
    def _embedding_inputs(request: OpenAIEmbeddingRequestModel) -> tuple[str, ...]:
        if isinstance(request.input, str):
            return (request.input,)
        return tuple(request.input)

    @staticmethod
    def _rerank_documents(request: OpenAIRerankRequestModel) -> tuple[str, ...]:
        return tuple(item if isinstance(item, str) else item.text for item in request.documents)

    @staticmethod
    def _embedding_response(
        model_code: str,
        result: object,
    ) -> OpenAIEmbeddingResponseModel:
        embed_result: ModelServiceEmbeddingResult = result.payload
        settled = result.settled
        return OpenAIEmbeddingResponseModel.model_validate(
            {
                'object': 'list',
                'data': [
                    {
                        'object': 'embedding',
                        'index': vector.index,
                        'embedding': list(vector.values),
                    }
                    for vector in embed_result.response.vectors
                ],
                'model': model_code,
                'usage': {
                    'prompt_tokens': settled.input_tokens or 0,
                    'total_tokens': settled.input_tokens or 0,
                },
            }
        )

    @staticmethod
    def _rerank_response(
        request: OpenAIRerankRequestModel,
        result: object,
        documents: tuple[str, ...],
    ) -> OpenAIRerankResponseModel:
        rerank_result: ModelServiceRerankResult = result.payload
        settled = result.settled
        return_documents = request.return_documents if request.return_documents is not None else False
        results = []
        for item in rerank_result.response.results:
            entry: dict[str, object] = {'index': item.index, 'relevance_score': item.relevance_score}
            if return_documents and 0 <= item.index < len(documents):
                entry['document'] = {'text': documents[item.index]}
            results.append(entry)
        meta: dict[str, object] = {
            'billed_units': {'search_units': settled.search_units or 1},
            'tokens': {'input_tokens': settled.input_tokens or 0},
        }
        return OpenAIRerankResponseModel.model_validate(
            {
                'id': rerank_result.response.request_id,
                'results': results,
                'meta': meta,
            }
        )

    def _completion_snapshot(self) -> tuple[str, int]:
        value = self._clock()
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        completion_id = self._completion_id_factory()
        created = int(normalized.timestamp())
        if not isinstance(completion_id, str) or _COMPLETION_ID_PATTERN.fullmatch(completion_id) is None or created < 0:
            raise ValueError('Invalid completion snapshot')
        return completion_id, created

    @staticmethod
    def _input_bytes(request: OpenAIChatCompletionRequestModel) -> int:
        return sum(len(message.content.encode('utf-8')) for message in request.messages)

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
        result: object,
    ) -> OpenAIChatCompletionResponseModel:
        settled = result.settled
        chat_result: ModelServiceChatResult = result.payload
        return OpenAIChatCompletionResponseModel.model_validate(
            {
                'id': completion_id,
                'object': 'chat.completion',
                'created': created,
                'model': model_code,
                'choices': [
                    {
                        'index': 0,
                        'message': {
                            'role': 'assistant',
                            'content': chat_result.response.text,
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {
                    'prompt_tokens': settled.input_tokens or 0,
                    'completion_tokens': settled.output_tokens or 0,
                    'total_tokens': (settled.input_tokens or 0) + (settled.output_tokens or 0),
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
            'request_too_large': PublicApiError.request_too_large,
            'rate_limit_exceeded': PublicApiError.rate_limit_exceeded,
            'concurrency_limit_exceeded': PublicApiError.concurrency_limit_exceeded,
            'budget_exceeded': PublicApiError.budget_exceeded,
        }
        return factories[error.code]()
