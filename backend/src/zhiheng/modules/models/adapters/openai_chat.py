from collections.abc import AsyncIterator, Mapping
from typing import Literal

from httpx2 import Request
from langchain_core.messages import AIMessage, BaseMessage, ChatMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from openai import APIError, APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient, DefaultHttpxClient, Omit, OpenAI
from pydantic import SecretStr

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


class OpenAIChatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_output_tokens: int,
        timeout_seconds: float,
        options: dict[str, object] | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = SecretStr(api_key)
        self._model = model
        self._max_output = max_output_tokens
        self._timeout = timeout_seconds
        self._options = options or {}

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
        raise ProviderError

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
                client = ChatOpenAI(
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
        normalized: list[BaseMessage] = []
        for message in messages:
            if message.role == 'system':
                normalized.append(SystemMessage(content=message.content))
            elif message.role == 'developer':
                normalized.append(ChatMessage(role='developer', content=message.content))
            elif message.role == 'assistant':
                normalized.append(AIMessage(content=message.content))
            else:
                normalized.append(HumanMessage(content=message.content))
        completed = False
        try:
            with tracing_context(enabled=False):
                async for chunk in client.bind(**self._options).astream(normalized):
                    if not isinstance(chunk.content, str):
                        raise ProviderError
                    usage = chunk.usage_metadata
                    input_tokens = captured.input_tokens if usage is not None else None
                    output_tokens = captured.output_tokens if usage is not None else None
                    finish_reason = self._completion_reason(chunk.response_metadata.get('finish_reason'))
                    completed = completed or finish_reason is not None
                    if chunk.content or usage is not None or finish_reason is not None:
                        yield ModelDelta(
                            chunk.content,
                            input_tokens,
                            output_tokens,
                            finish_reason,
                            cache_tokens=captured.cache_tokens,
                            reasoning_tokens=captured.reasoning_tokens,
                        )
            if not completed:
                raise ProviderError
        except APIStatusError as exc:
            raise ProviderError(definitive_rejection=exc.status_code in {400, 401, 403, 404, 422, 429}) from None
        except (APIError, TimeoutError, ValueError):
            raise ProviderError from None
