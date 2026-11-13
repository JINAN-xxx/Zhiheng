"""Pinned SDK execution and callbacks live exclusively in one owned thread loop."""

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any

import numpy as np

from lightrag.kg.shared_storage import initialize_share_data  # type: ignore[import-untyped]
from zhiheng.modules.knowledge.adapters.lightrag.factory import create_rag, run_operation
from zhiheng.modules.knowledge.adapters.lightrag.gc import delete_generation
from zhiheng.modules.knowledge.adapters.lightrag.lifecycle import AttemptResources
from zhiheng.modules.knowledge.adapters.lightrag.owned_loop import OwnedSDKLoop
from zhiheng.modules.knowledge.adapters.lightrag.postgres import CURRENT_STORAGE, PostgresContext
from zhiheng.modules.knowledge.adapters.lightrag.registry import register
from zhiheng.modules.knowledge.adapters.lightrag.tokenizer import OfficialBPETokenizer
from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
from zhiheng.modules.knowledge.adapters.lightrag.vector import CURRENT_VECTOR, VectorContext
from zhiheng.modules.knowledge.control import VectorIOControl
from zhiheng.modules.knowledge.graph.contracts import (
    GraphFrozen,
    GraphModelRole,
    GraphSDKCall,
    GraphSDKOutcome,
    GraphVectorManifest,
)
from zhiheng.modules.knowledge.ranking import ranking_rows
from zhiheng.modules.models.public import ModelMessage
from zhiheng.shared.errors import AppError


async def _model_bridge(
    model: Callable[[], Awaitable[object]], main_loop: asyncio.AbstractEventLoop, *, timeout: float
) -> object:
    """Cancellation retains ownership until the actual model callback has drained."""
    result: concurrent.futures.Future[object] = concurrent.futures.Future()
    tasks: list[asyncio.Task[None]] = []

    async def invoke() -> None:
        try:
            result.set_result(await model())
        except BaseException as error:
            result.set_exception(error)

    def submit() -> None:
        tasks.append(main_loop.create_task(invoke()))

    def cancel_actual() -> None:
        tasks[0].cancel()

    main_loop.call_soon_threadsafe(submit)
    future = asyncio.wrap_future(result)
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except (asyncio.CancelledError, TimeoutError):
        # The operation deadline ends business waiting, while actual cleanup
        # retains ownership just as it does in OwnedSDKLoop.run.
        # submit precedes cancellation in the same loop's queue. Never cancel
        # the result future: it must observe the real callback's final outcome.
        main_loop.call_soon_threadsafe(cancel_actual)
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:  # noqa: PERF203 - repeated caller cancel must not abandon model cleanup
                continue
            except BaseException:
                break
        future.exception()
        raise


def _callbacks(
    model: Callable[[GraphSDKCall], Awaitable[object]],
    main_loop: asyncio.AbstractEventLoop,
    control: VectorIOControl,
    dimension: int,
) -> tuple[Callable[..., Awaitable[str]], Callable[..., Awaitable[Any]]]:
    async def callback(call: GraphSDKCall) -> object:
        remaining = control.remaining_operation()

        async def invoke() -> object:
            control.check_operation()
            return await model(call)

        return await _model_bridge(invoke, main_loop, timeout=remaining)

    async def llm(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        _role: GraphModelRole = 'extract',
        **kwargs: Any,
    ) -> str:
        if _role not in {'extract', 'keyword', 'query', 'vlm'}:
            raise AppError('unsupported_graph_role', 'SDK模型角色无效', 422)
        unknown = set(kwargs) - {'hashing_kv', 'max_tokens', 'response_format', 'keyword_extraction'}
        if unknown:
            raise AppError('unsupported_graph_options', 'SDK生成参数未受支持', 422)
        output = kwargs.get('max_tokens')
        if output is not None and (type(output) is not int or output < 1):
            raise AppError('unsupported_graph_options', 'SDK输出上限无效', 422)
        response = kwargs.get('response_format')
        if response is not None and response != {'type': 'json_object'}:
            raise AppError('unsupported_graph_options', 'SDK结构化参数无效', 422)
        messages = []
        if system_prompt:
            messages.append(ModelMessage('system', system_prompt))
        for item in history_messages or []:
            if (
                set(item) != {'role', 'content'}
                or item['role'] not in {'system', 'user', 'assistant', 'developer'}
                or not isinstance(item['content'], str)
            ):
                raise AppError('unsupported_graph_history', 'SDK历史消息无效', 422)
            messages.append(ModelMessage(item['role'], item['content']))  # type: ignore[arg-type]
        messages.append(ModelMessage('user', prompt))
        result = await callback(
            GraphSDKCall(
                _role,
                'keyword' if response else 'generation',
                tuple(messages),
                kwargs.get('max_tokens'),
                bool(response),
            )
        )
        if not isinstance(result, str):
            raise AppError('graph_model_invalid', '图谱模型正文无效', 502)
        return result

    async def embedding(texts: list[str], **kwargs: Any) -> Any:
        requested_dimension = kwargs.get('embedding_dim')
        if requested_dimension is not None and (
            type(requested_dimension) is not int or requested_dimension != dimension
        ):
            raise AppError('unsupported_graph_options', 'SDK向量维度与冻结版本不一致', 422)
        if set(kwargs) - {'embedding_dim', 'max_token_size', 'context', 'priority', '_priority'}:
            raise AppError('unsupported_graph_options', 'SDK向量参数无效', 422)
        value = await callback(
            GraphSDKCall('embedding', 'embedding', tuple(ModelMessage('user', text) for text in texts))
        )
        return np.asarray(value, dtype=np.float32)

    return llm, embedding


def _rerank_callback(
    model: Callable[[GraphSDKCall], Awaitable[object]],
    main_loop: asyncio.AbstractEventLoop,
    control: VectorIOControl,
    frozen: GraphFrozen,
    diagnostics: dict[str, Any],
    failures: list[Exception],
) -> Callable[..., Awaitable[list[dict[str, object]]]]:
    async def rerank(*, query: str, documents: list[str], top_n: int = 8, **kwargs: Any) -> list[dict[str, object]]:
        started = monotonic()
        try:
            control.check_operation()
            if kwargs or any(text not in {chunk.content for chunk in frozen.chunks} for text in documents):
                raise AppError('graph_provenance_invalid', '图谱重排候选包含未授权来源', 503)
            candidates = documents[:40]
            diagnostics['candidate_count'] = len(candidates)

            async def invoke() -> object:
                control.check_operation()
                return await model(
                    GraphSDKCall(
                        'rerank',
                        'rerank',
                        (ModelMessage('user', query), *(ModelMessage('user', text) for text in candidates)),
                    )
                )

            result = await _model_bridge(invoke, main_loop, timeout=control.remaining_operation())
            if isinstance(result, dict) and result.get('_rerank_fallback') == 'knowledge_rerank_transient':
                raise AppError('knowledge_rerank_failed', '图谱重排服务暂时不可用，本次未返回检索结果', 502)
            rows = ranking_rows(result, len(candidates))[: min(8, top_n)]
            diagnostics.update(
                rerank_status='completed',
                selected_contents=[candidates[index] for index, _ in rows],
            )
            return [{'index': index, 'relevance_score': score} for index, score in rows]
        except Exception as error:
            # LightRAG catches Exception and falls back unconditionally. Retain a
            # fatal error to rethrow before its result crosses our application port.
            failures.append(error)
            raise
        finally:
            diagnostics['rerank_milliseconds'] = int((monotonic() - started) * 1000)

    return rerank


class GraphSDK:
    def __init__(
        self,
        *,
        database_url: str | None,
        milvus_uri: str | None,
        milvus_token: str,
        milvus_database: str,
        working_root: Path,
    ) -> None:
        self.database_url, self.uri, self.token, self.database, self.root = (
            database_url,
            milvus_uri,
            milvus_token,
            milvus_database,
            working_root,
        )
        self.owned_loop = OwnedSDKLoop()

    async def available(self) -> bool:
        if not self.database_url or not self.uri:
            return False
        try:

            async def check() -> None:
                OfficialBPETokenizer()
                context = PostgresContext(
                    url=self.database_url or '', workspace='availability', read_only=True, check=lambda: None
                )
                await context.open()
                await context.close()

            register()
            await asyncio.to_thread(lambda: asyncio.run(check()))
            rpc = GraphVectorRPC(
                uri=self.uri, token=self.token, database=self.database, control=VectorIOControl(timeout_seconds=3)
            )
            try:
                await rpc.acall('get_server_version', timeout=3)
            finally:
                await rpc.aclose()
            return True
        except Exception:
            return False

    async def execute(
        self,
        *,
        operation: str,
        frozen: GraphFrozen,
        manifest: GraphVectorManifest,
        request: dict[str, object],
        model: Callable[[GraphSDKCall], Awaitable[object]],
        control: VectorIOControl,
        fence: Callable[[], Awaitable[None]] | None = None,
        stopped: Callable[[], None] | None = None,
    ) -> GraphSDKOutcome:
        if not self.database_url or not self.uri:
            if stopped is not None:
                stopped()
            raise AppError('graph_unavailable', '未配置图谱运行连接', 503)
        if operation == 'query' and request.get('_rerank_binding') is None:
            if stopped is not None:
                stopped()
            raise AppError('knowledge_rerank_required', '图谱检索必须配置重排模型', 409)
        main_loop = asyncio.get_running_loop()

        llm, embedding = _callbacks(model, main_loop, control, manifest.dimension)
        rerank_state: dict[str, Any] = {}
        rerank_failures: list[Exception] = []
        rerank = (
            _rerank_callback(model, main_loop, control, frozen, rerank_state, rerank_failures)
            if operation == 'query' and request.get('_rerank_binding') is not None
            else None
        )

        def check() -> None:
            control.check_operation()
            if fence is not None:

                async def verify() -> None:
                    await fence()

                asyncio.run_coroutine_threadsafe(verify(), main_loop).result()

        async def program() -> GraphSDKOutcome:
            async with AttemptResources(control, stopped) as resources:
                register()
                initialize_share_data()
                resources.context = PostgresContext(
                    url=self.database_url or '',
                    workspace=manifest.workspace,
                    read_only=operation in {'query', 'labels', 'subgraph'},
                    check=check,
                )
                await resources.context.open()
                resources.tokens.callback(CURRENT_STORAGE.reset, CURRENT_STORAGE.set(resources.context))
                vector = VectorContext(
                    self.uri or '',
                    self.token,
                    self.database,
                    manifest,
                    control,
                    operation in {'build', 'preview'},
                    resources.rpcs,
                )
                resources.tokens.callback(CURRENT_VECTOR.reset, CURRENT_VECTOR.set(vector))
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                resources.temporary = TemporaryDirectory(dir=self.root, prefix=manifest.attempt_id.hex + '-')
                resources.rag = create_rag(
                    root=Path(resources.temporary.name),
                    frozen=frozen,
                    manifest=manifest,
                    llm=llm,
                    embedding=embedding,
                    rerank=rerank,
                )
                await resources.rag.initialize_storages()
                result = await run_operation(
                    resources.rag,
                    operation,
                    frozen,
                    manifest,
                    request,
                    rerank_state=rerank_state,
                )
                if rerank_failures:
                    raise rerank_failures[0]
                resources.completed = True
                return GraphSDKOutcome(result, {'workspace': manifest.workspace}, True)

        return await self.owned_loop.run(program)

    async def close(self) -> None:
        await self.owned_loop.close()

    async def gc(self, manifest: GraphVectorManifest) -> None:
        if not self.database_url or not self.uri:
            raise AppError('graph_unavailable', '未配置图谱运行连接', 503)
        try:
            await delete_generation(
                url=self.database_url, uri=self.uri, token=self.token, database=self.database, manifest=manifest
            )
        except AppError:
            raise
        except Exception:
            raise AppError('graph_gc_unavailable', '图谱回收连接不可用', 503) from None
