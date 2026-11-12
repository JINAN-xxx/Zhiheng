from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal

from httpx2 import Request
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    BaseMessageChunk,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, Omit, OpenAI
from pydantic import SecretStr

from zhiheng.modules.models.adapters.catalog import validate_catalog
from zhiheng.modules.models.adapters.errors import provider_error
from zhiheng.modules.models.adapters.usage import CapturingCompletions, UsageCapture
from zhiheng.modules.models.contracts import ModelDelta, ModelMessage, ProviderError


def _connection_headers(authentication: Mapping[str, str]) -> dict[str, str | Omit]:
    return {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'zhiheng-platform/0.1.0',
        **authentication,
    }


class _ConnectionClient(OpenAI):
    @property
    def default_headers(self) -> dict[str, str | Omit]:
        # Public SDK policy boundary, before HTTP header encoding/validation.
        return _connection_headers(self.auth_headers)


class _AsyncConnectionClient(AsyncOpenAI):
    @property
    def default_headers(self) -> dict[str, str | Omit]:
        return _connection_headers(self.auth_headers)


class _ReasoningChatOpenAI(ChatOpenAI):
    """LC 1.4.7 drops provider reasoning_content in its chat delta conversion."""

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        *,
        stream_usage: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if kwargs.get('response_format') != {'type': 'json_object'}:
            async for chunk in super()._astream(
                messages, stop=stop, run_manager=run_manager, stream_usage=stream_usage, **kwargs
            ):
                yield chunk
            return
        # LC sends response_format through root.beta.stream, bypassing the raw
        # usage client and parsing JSON even when the platform needs plain text.
        # Keep LC's payload/conversion while using the same captured SSE path.
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        payload.update(stream=True, stream_options={'include_usage': True})
        response = await self.async_client.create(**payload)
        default_chunk: type[BaseMessageChunk] = AIMessageChunk
        async with response:
            async for raw in response:
                converted = self._convert_chunk_to_generation_chunk(raw, default_chunk, {})
                if converted is not None:
                    default_chunk = converted.message.__class__
                    if run_manager is not None:
                        await run_manager.on_llm_new_token(converted.text, chunk=converted)
                    yield converted

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type[BaseMessageChunk],
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        result = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        choices = chunk.get('choices')
        if result is not None and choices:
            delta = choices[0].get('delta') or {}
            reasoning = delta.get('reasoning_content')
            if reasoning is not None:
                if not isinstance(reasoning, str):
                    raise ProviderError(reason='invalid_reasoning')
                result.message.additional_kwargs['reasoning_content'] = reasoning
        return result


class OpenAIChatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_output_tokens: int | None,
        timeout_seconds: float,
        options: dict[str, object] | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = SecretStr(api_key)
        self._model = model
        self._max_output = max_output_tokens
        self._timeout = timeout_seconds
        self._options = dict(options or {})
        self._options.pop('provider_output_default', None)
        response_format = self._options.get('response_format')
        if response_format is not None:
            if response_format != 'json_object':
                raise ProviderError(definitive_rejection=True, reason='invalid_options')
            self._options['response_format'] = {'type': 'json_object'}

    def _isolate_request(self, request: Request) -> None:
        # The SDK merges OPENAI_CUSTOM_HEADERS after computing authentication.
        # Enforce the connection's complete wire policy at the final HTTP boundary.
        request.headers.clear()
        request.headers.update(
            {
                'Host': request.url.netloc.decode('ascii'),
                'Authorization': f'Bearer {self._api_key.get_secret_value()}',
                'Content-Type': 'application/json',
                'Content-Length': str(len(request.content)),
                'Accept': 'text/event-stream',
                'User-Agent': 'zhiheng-platform/0.1.0',
            }
        )

    async def _isolate_async_request(self, request: Request) -> None:
        self._isolate_request(request)

    @staticmethod
    def _completion_reason(value: object) -> Literal['stop', 'length'] | None:
        if value is None:
            return None
        if value == 'stop':
            return 'stop'
        if value == 'length':
            return 'length'
        raise ProviderError(reason='unsupported_finish_reason')

    async def validate(self) -> None:
        await validate_catalog(
            base_url=self._base_url,
            api_key=self._api_key.get_secret_value(),
            model=self._model,
            timeout_seconds=self._timeout,
        )

    async def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
        usage_capture = UsageCapture()
        # Each invocation owns these clients. LangChain's implicit HTTP cache must
        # not be closed by one invocation while another is still using it.
        with _ConnectionClient(
            api_key=self._api_key.get_secret_value(),
            admin_api_key='',
            organization='',
            project='',
            webhook_secret='',
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            default_headers={'Authorization': f'Bearer {self._api_key.get_secret_value()}'},
            http_client=DefaultHttpxClient(
                trust_env=False, follow_redirects=False, event_hooks={'request': [self._isolate_request]}
            ),
        ) as sync_client:
            async with _AsyncConnectionClient(
                api_key=self._api_key.get_secret_value(),
                admin_api_key='',
                organization='',
                project='',
                webhook_secret='',
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=0,
                default_headers={'Authorization': f'Bearer {self._api_key.get_secret_value()}'},
                http_client=DefaultAsyncHttpxClient(
                    trust_env=False, follow_redirects=False, event_hooks={'request': [self._isolate_async_request]}
                ),
            ) as async_client:
                client = _ReasoningChatOpenAI(
                    model=self._model,
                    api_key=self._api_key,
                    base_url=self._base_url,
                    client=sync_client.chat.completions,
                    async_client=CapturingCompletions(async_client, usage_capture),
                    root_client=sync_client,
                    root_async_client=async_client,
                    organization='',
                    openai_proxy='',
                    max_completion_tokens=self._max_output,
                    max_retries=0,
                    timeout=self._timeout,
                    stream_chunk_timeout=self._timeout,
                    stream_usage=True,
                    use_responses_api=False,
                    output_version='v0',
                )
                async for delta in self._read(client, messages, usage_capture):
                    yield delta

    async def _read(
        self, client: ChatOpenAI, messages: tuple[ModelMessage, ...], captured: UsageCapture
    ) -> AsyncIterator[ModelDelta]:
        normalized = normalize_messages(messages)
        completed = False
        failure: ProviderError | None = None
        try:
            with tracing_context(enabled=False):
                async for chunk in client.bind(**self._options).astream(normalized):
                    if not isinstance(chunk.content, str):
                        raise ProviderError(reason='invalid_content')
                    finish_reason = self._completion_reason(chunk.response_metadata.get('finish_reason'))
                    completed = completed or finish_reason is not None
                    reasoning = chunk.additional_kwargs.get('reasoning_content', '')
                    if not isinstance(reasoning, str):
                        raise ProviderError(reason='invalid_reasoning')
                    if chunk.content or reasoning or finish_reason is not None:
                        yield ModelDelta(
                            chunk.content,
                            finish_reason=finish_reason,
                            reasoning=reasoning,
                        )
        except (APIError, TimeoutError, ValueError) as exc:
            failure = provider_error(exc)
        if captured.observed:
            yield ModelDelta(
                input_tokens=captured.input_tokens,
                output_tokens=captured.output_tokens,
                cache_tokens=captured.cache_tokens,
                reasoning_tokens=captured.reasoning_tokens,
            )
        if failure is not None:
            raise failure from None
        if not completed:
            raise ProviderError(reason='incomplete_stream')


def normalize_messages(messages: tuple[ModelMessage, ...]) -> list[BaseMessage]:
    normalized: list[BaseMessage] = []
    for message in messages:
        if message.role == 'system':
            normalized.append(SystemMessage(content=message.content))
        elif message.role == 'developer':
            normalized.append(ChatMessage(role='developer', content=message.content))
        elif message.role == 'assistant':
            normalized.append(AIMessage(content=message.content))
        elif message.images:
            blocks: list[str | dict[str, Any]] = [{'type': 'text', 'text': message.content}]
            blocks.extend(
                {'type': 'image_url', 'image_url': {'url': image.data_url, 'detail': 'auto'}}
                for image in message.images
            )
            normalized.append(HumanMessage(content=blocks))
        else:
            normalized.append(HumanMessage(content=message.content))
    return normalized
