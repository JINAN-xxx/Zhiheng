"""Per-invocation isolated HTTP transport and typed LangChain embedding adapter."""

import math
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from langchain_openai import OpenAIEmbeddings
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from zhiheng.modules.models.adapters.usage import normalize_counter
from zhiheng.modules.models.contracts import ModelDelta, ModelMessage, ProviderError


class EmbeddingRow(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class EmbeddingResponse(BaseModel):
    data: list[EmbeddingRow]
    usage: dict[str, object] | None = None


class RerankRow(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    index: int = Field(ge=0)
    relevance_score: float


class RerankResponse(BaseModel):
    model_config = ConfigDict(extra='ignore')
    results: list[RerankRow]
    meta: dict[str, dict[str, object]] | None = None
    usage: dict[str, object] | None = None


class EmbeddingTransport:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.usage: int | None = None

    async def create(self, **kwargs: object) -> dict[str, object]:
        response = await self.client.post('embeddings', json=kwargs)
        response.raise_for_status()
        result = EmbeddingResponse.model_validate(response.json())
        value = (result.usage or {}).get('prompt_tokens')
        self.usage = normalize_counter(value)
        if [row.index for row in sorted(result.data, key=lambda item: item.index)] != list(range(len(result.data))):
            raise ProviderError
        if not result.data or any(not math.isfinite(value) for row in result.data for value in row.embedding):
            raise ProviderError
        return {'data': [row.model_dump() for row in sorted(result.data, key=lambda item: item.index)]}


class StructuredProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        model_type: Literal['embedding', 'rerank'],
        options: dict[str, object],
    ) -> None:
        self._base_url, self._key, self._model = base_url.rstrip('/') + '/', SecretStr(api_key), model
        self._timeout, self._type, self._options = timeout_seconds, model_type, options

    async def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
        headers = {
            'Authorization': 'Bearer ' + self._key.get_secret_value(),
            'Content-Type': 'application/json',
            'User-Agent': 'zhiheng-platform/0.1.0',
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, headers=headers, trust_env=False, follow_redirects=False, timeout=self._timeout
            ) as client:
                if self._type == 'embedding':
                    # Leading slash must not discard a configured /v1 prefix.
                    transport = EmbeddingTransport(client)
                    embedder = OpenAIEmbeddings(
                        model=self._model,
                        openai_api_key=self._key,
                        openai_api_base=self._base_url,
                        client=transport,
                        async_client=transport,
                        openai_proxy='',
                        openai_api_type='',
                        openai_api_version='',
                        openai_organization='',
                        check_embedding_ctx_length=False,
                        chunk_size=256,
                        max_retries=0,
                    )
                    options = {key: value for key, value in self._options.items() if key in {'dimensions', 'user'}}
                    with tracing_context(enabled=False):
                        vectors = await embedder.aembed_documents(
                            [message.content for message in messages], chunk_size=256, **options
                        )
                    dimensions = self._options.get('dimensions')
                    if (
                        len(vectors) != len(messages)
                        or len({len(vector) for vector in vectors}) != 1
                        or (isinstance(dimensions, int) and len(vectors[0]) != dimensions)
                    ):
                        raise ProviderError
                    yield ModelDelta(
                        input_tokens=transport.usage,
                        output_tokens=0 if transport.usage is not None else None,
                        result={
                            'data': [
                                {'object': 'embedding', 'index': index, 'embedding': vector}
                                for index, vector in enumerate(vectors)
                            ]
                        },
                        finish_reason='stop',
                    )
                else:
                    payload: dict[str, object] = {
                        'model': self._model,
                        'query': messages[0].content,
                        'documents': [message.content for message in messages[1:]],
                        'top_n': self._options.get('top_n', len(messages) - 1),
                    }
                    response = await client.post('rerank', json=payload)
                    response.raise_for_status()
                    result = RerankResponse.model_validate(response.json())
                    if len({row.index for row in result.results}) != len(result.results) or any(
                        row.index >= len(messages) - 1 or not math.isfinite(row.relevance_score)
                        for row in result.results
                    ):
                        raise ProviderError
                    units = normalize_counter(((result.meta or {}).get('billed_units') or {}).get('search_units'))
                    rows: list[dict[str, object]] = []
                    for row in result.results:
                        item: dict[str, object] = row.model_dump()
                        if self._options.get('return_documents'):
                            item['document'] = {'text': messages[row.index + 1].content}
                        rows.append(item)
                    usage = result.usage or (result.meta or {}).get('tokens') or {}
                    prompt = usage.get('prompt_tokens', usage.get('input_tokens'))
                    completion = usage.get('completion_tokens', usage.get('output_tokens'))
                    prompt = normalize_counter(prompt)
                    completion = normalize_counter(completion)
                    yield ModelDelta(
                        input_tokens=prompt,
                        output_tokens=completion,
                        result={
                            'results': rows,
                            'meta': {
                                'billed_units': {'search_units': units},
                                'tokens': {'input_tokens': prompt, 'output_tokens': completion},
                            },
                        },
                        search_units=units,
                        finish_reason='stop',
                    )
        except httpx.HTTPStatusError as error:
            raise ProviderError(
                definitive_rejection=error.response.status_code in {400, 401, 403, 404, 422, 429}
            ) from None
        except (httpx.HTTPError, ValueError, KeyError):
            raise ProviderError from None
