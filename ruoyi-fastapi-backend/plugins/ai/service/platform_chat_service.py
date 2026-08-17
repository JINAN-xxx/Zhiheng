import asyncio
import base64
import re
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar, NoReturn, Protocol
from uuid import UUID

from plugins.ai.dao.platform_chat_dao import PlatformChatDao
from plugins.ai.domain.model_runtime import ModelMessage, ModelStreamEvent
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage, AiChatRun, AiChatSession
from plugins.ai.entity.vo.ai_platform_chat_vo import PlatformChatRequestModel
from plugins.ai.service.chat_preferences_service import ChatPreferencesService
from plugins.ai.service.chat_runtime_coordinator import ChatCoordinationError, ChatRuntimeCoordinator
from plugins.ai.service.model_service import ModelServiceStreamResult, ResolvedModel

_ERROR_CONTRACTS = (
    (404, 'CHAT_SESSION_NOT_FOUND', '对话会话不存在'),
    (409, 'CHAT_SESSION_MODEL_MISMATCH', '对话会话不能切换模型'),
    (409, 'CHAT_IDEMPOTENCY_CONFLICT', '客户端消息已存在且内容不一致'),
    (422, 'CHAT_IMAGE_INVALID', '图片引用无效或不可用'),
    (422, 'CHAT_VISION_DISABLED', '当前用户未启用视觉能力'),
)
_ERROR_NAMES = (
    'session_not_found',
    'session_model_mismatch',
    'idempotency_conflict',
    'image_invalid',
    'vision_disabled',
)
_ERROR_VIEW = MappingProxyType(dict(zip(_ERROR_NAMES, _ERROR_CONTRACTS, strict=True)))
_ERROR_TOKEN = object()
_DATA_URL_PATTERN = re.compile(r'^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$')
_MAX_IMAGE_BYTES = 50 * 1024 * 1024


def _chat_error_contract(index: object) -> tuple[int, str, str]:
    if type(index) is not int or not 0 <= index < len(_ERROR_CONTRACTS):
        raise TypeError('无效的对话错误分类')
    return _ERROR_CONTRACTS[index]


def _new_chat_error(index: int, token: object = _ERROR_TOKEN) -> 'PlatformChatError':
    return PlatformChatError(index, token)


def _trusted_chat_error(error: 'PlatformChatError') -> int:
    if type(error) is not PlatformChatError:
        raise TypeError('无效的对话错误实例')
    index = object.__getattribute__(error, '_kind_index')
    _chat_error_contract(index)
    if object.__getattribute__(error, 'args') != ():
        raise TypeError('无效的对话错误实例')
    return index


class PlatformChatError(Exception):
    """Platform Chat 固定、不可变且脱敏的请求错误。"""

    __slots__ = ('_kind_index',)
    _ERRORS: ClassVar = _ERROR_VIEW

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError('PlatformChatError 不允许继承')

    def __init__(self, index: int, token: object | None = None) -> None:
        if token is not _ERROR_TOKEN:
            raise TypeError('PlatformChatError 只能通过固定工厂创建')
        _chat_error_contract(index)
        object.__setattr__(self, '_kind_index', index)
        object.__setattr__(self, 'args', ())

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError('PlatformChatError 实例不可修改')

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError('PlatformChatError 实例不可修改')

    @property
    def status_code(self) -> int:
        return _chat_error_contract(_trusted_chat_error(self))[0]

    @property
    def code(self) -> str:
        return _chat_error_contract(_trusted_chat_error(self))[1]

    @property
    def public_message(self) -> str:
        return _chat_error_contract(_trusted_chat_error(self))[2]

    def __str__(self) -> str:
        return self.public_message

    def __repr__(self) -> str:
        return f'PlatformChatError(code={self.code!r}, message={self.public_message!r})'

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.public_message}

    @staticmethod
    def session_not_found() -> 'PlatformChatError':
        return _new_chat_error(0)

    @staticmethod
    def session_model_mismatch() -> 'PlatformChatError':
        return _new_chat_error(1)

    @staticmethod
    def idempotency_conflict() -> 'PlatformChatError':
        return _new_chat_error(2)

    @staticmethod
    def image_invalid() -> 'PlatformChatError':
        return _new_chat_error(3)

    @staticmethod
    def vision_disabled() -> 'PlatformChatError':
        return _new_chat_error(4)


@dataclass(frozen=True, slots=True)
class PlatformChatEvent:
    event_type: str
    data: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.event_type not in {'meta', 'content', 'reasoning', 'usage', 'error', 'done'}:
            raise ValueError('不支持的平台对话事件类型')
        object.__setattr__(self, 'data', MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class ResolvedPlatformImage:
    file_id: UUID
    mime_type: str
    size_bytes: int
    data_url: str


class PlatformImageResolver(Protocol):
    async def resolve(self, user_id: int, file_id: UUID, *, max_size_bytes: int) -> ResolvedPlatformImage: ...


class ChatDao(Protocol):
    async def get_owned_session_for_update(
        self, db: object, session_id: str, *, user_id: int
    ) -> AiChatSession | None: ...

    async def create_session(self, db: object, session: AiChatSession) -> AiChatSession: ...

    async def get_message_by_client_id(
        self, db: object, session_id: str, client_message_id: str
    ) -> AiChatMessage | None: ...

    async def create_message(self, db: object, message: AiChatMessage) -> AiChatMessage: ...

    async def list_history_messages(self, db: object, session_id: str, *, limit: int) -> list[AiChatMessage]: ...

    async def create_run(self, db: object, run: AiChatRun) -> AiChatRun: ...

    async def get_latest_run(self, db: object, session_id: str) -> AiChatRun | None: ...

    async def transition_run_status(
        self,
        db: object,
        run_id: str,
        *,
        expected_statuses: set[str],
        values: dict[str, Any],
    ) -> int: ...


class ChatModelService(Protocol):
    async def resolve_model(self, model_id: str) -> ResolvedModel: ...

    async def stream_chat(
        self,
        model_id: str,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceStreamResult: ...


class ChatPreferences(Protocol):
    async def get_preferences(self, *, user_id: int) -> Any: ...


def _freeze_data(data: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(data))


class PlatformChatStreamResult:
    def __init__(self, session_id: str, run_id: str, stream: AsyncIterator[PlatformChatEvent]) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self._stream = stream
        self._iterator = stream.__aiter__()
        self._lock = asyncio.Lock()
        self._closed = False

    def __aiter__(self) -> 'PlatformChatStreamResult':
        return self

    async def __anext__(self) -> PlatformChatEvent:
        async with self._lock:
            if self._closed:
                raise StopAsyncIteration
            try:
                return await anext(self._iterator)
            except StopAsyncIteration:
                self._closed = True
                raise
            except BaseException:
                await self._close_iterator_preserving_error()
                raise

    async def aclose(self) -> None:
        async with self._lock:
            await self._close_iterator_locked()

    async def _close_iterator_locked(self) -> None:
        if self._closed:
            return
        close = getattr(self._iterator, 'aclose', None)
        if close is not None:
            await close()
        self._closed = True

    async def _close_iterator_preserving_error(self) -> None:
        try:
            await self._close_iterator_locked()
        except BaseException:
            pass


@dataclass
class _GenerationState:
    content_parts: list[str]
    reasoning_parts: list[str]
    usage: Any = None
    final_status: str = 'completed'
    active_error: BaseException | None = None
    error_code: str | None = None
    error_message: str | None = None


def _validate_data_url(image: ResolvedPlatformImage, *, max_size_bytes: int) -> bool:
    if image.size_bytes < 0 or image.size_bytes > max_size_bytes or image.size_bytes > _MAX_IMAGE_BYTES:
        return False
    match = _DATA_URL_PATTERN.fullmatch(image.data_url)
    if match is None or match.group(1) != image.mime_type:
        return False
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except (ValueError, TypeError):
        return False
    return len(decoded) == image.size_bytes


class PlatformChatService:
    def __init__(
        self,
        db: object,
        *,
        model_service: ChatModelService,
        coordinator: ChatRuntimeCoordinator,
        preferences: ChatPreferences | ChatPreferencesService,
        chat_dao: ChatDao = PlatformChatDao,
        image_resolver: PlatformImageResolver | None = None,
        id_factory: Callable[[str], str] = new_resource_id,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._db = db
        self._model_service = model_service
        self._coordinator = coordinator
        self._preferences = preferences
        self._dao = chat_dao
        self._image_resolver = image_resolver
        self._id_factory = id_factory
        self._clock = clock

    async def send(self, user_id: int, request: PlatformChatRequestModel) -> PlatformChatStreamResult:
        try:
            return await self._send(user_id, request)
        except BaseException:
            await self._rollback_preserving_error()
            raise

    async def _send(self, user_id: int, request: PlatformChatRequestModel) -> PlatformChatStreamResult:
        session = await self._load_owned_session(user_id, request.session_id)
        resolved = await self._model_service.resolve_model(request.model_id)
        if session is not None and session.model_id != resolved.platform_model_id:
            raise PlatformChatError.session_model_mismatch()
        if session is None:
            session = AiChatSession(
                session_id=self._id_factory('ses'),
                user_id=user_id,
                model_id=resolved.platform_model_id,
                status='active',
                title=request.message[:200],
                created_at=self._clock(),
                updated_at=self._clock(),
            )
            await self._dao.create_session(self._db, session)
        preferences = await self._preferences.get_preferences(user_id=user_id)
        existing = await self._dao.get_message_by_client_id(
            self._db,
            session.session_id,
            request.client_message_id,
        )
        if existing is not None:
            if existing.content != request.message:
                raise PlatformChatError.idempotency_conflict()
            previous_run = await self._dao.get_latest_run(self._db, session.session_id)
            await self._rollback_if_active()
            replay_run_id = previous_run.run_id if previous_run is not None else self._id_factory('run')
            return PlatformChatStreamResult(
                session.session_id, replay_run_id, self._replay(session.session_id, replay_run_id)
            )

        history = []
        if preferences.add_history and preferences.history_message_count > 0:
            history = await self._dao.list_history_messages(
                self._db,
                session.session_id,
                limit=preferences.history_message_count,
            )
        user_content, image_refs = await self._build_user_content(user_id, request, preferences)
        message = AiChatMessage(
            message_id=self._id_factory('msg'),
            session_id=session.session_id,
            client_message_id=request.client_message_id,
            role='user',
            content=request.message,
            images=image_refs,
            status='complete',
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        await self._dao.create_message(self._db, message)
        run = AiChatRun(
            run_id=self._id_factory('run'),
            session_id=session.session_id,
            model_id=resolved.platform_model_id,
            binding_id=resolved.binding_id,
            engine_type=resolved.engine_type,
            engine_model_snapshot=resolved.engine_model_snapshot,
            status='pending',
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        await self._dao.create_run(self._db, run)
        await self._commit()

        messages = self._build_messages(history, request.message, user_content, preferences.system_prompt)
        parameters = {'temperature': preferences.temperature} if preferences.temperature is not None else {}
        lock = self._coordinator.acquire_session(session.session_id)
        try:
            await lock.__aenter__()
            runtime_stream = await self._model_service.stream_chat(
                resolved.platform_model_id,
                tuple(messages),
                parameters,
            )
        except BaseException as error:
            await self._finish_setup_failure(run.run_id, error)
            try:
                await lock.__aexit__(type(error), error, error.__traceback__)
            except BaseException:
                pass
            raise
        return PlatformChatStreamResult(
            session.session_id,
            run.run_id,
            self._generate(
                session=session,
                run=run,
                request=request,
                runtime_stream=runtime_stream,
                lock=lock,
            ),
        )

    async def _load_owned_session(self, user_id: int, session_id: str | None) -> AiChatSession | None:
        if session_id is None:
            return None
        session = await self._dao.get_owned_session_for_update(self._db, session_id, user_id=user_id)
        if session is None:
            raise PlatformChatError.session_not_found()
        if session.status != 'active':
            raise PlatformChatError.session_not_found()
        return session

    async def _build_user_content(
        self,
        user_id: int,
        request: PlatformChatRequestModel,
        preferences: Any,
    ) -> tuple[object, list[dict[str, str]]]:
        if not request.images:
            return request.message, []
        if not preferences.vision_enabled:
            raise PlatformChatError.vision_disabled()
        if self._image_resolver is None:
            raise PlatformChatError.image_invalid()
        max_size = preferences.image_max_size_mb * 1024 * 1024
        blocks: list[dict[str, object]] = [{'type': 'text', 'text': request.message}]
        image_refs: list[dict[str, str]] = []
        for reference in request.images:
            image = await self._image_resolver.resolve(user_id, reference.file_id, max_size_bytes=max_size)
            if image.file_id != reference.file_id or not _validate_data_url(image, max_size_bytes=max_size):
                raise PlatformChatError.image_invalid()
            blocks.append({'type': 'image_url', 'image_url': {'url': image.data_url}})
            image_refs.append(
                {'fileId': str(image.file_id), 'mimeType': image.mime_type, 'sizeBytes': str(image.size_bytes)}
            )
        return blocks, image_refs

    @staticmethod
    def _build_messages(
        history: Sequence[AiChatMessage],
        message: str,
        user_content: object,
        system_prompt: str | None,
    ) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        if system_prompt:
            messages.append(ModelMessage(role='system', content=system_prompt))
        messages.extend(
            ModelMessage(role=item.role, content=item.content)
            for item in history
            if item.role in {'user', 'assistant'} and item.status == 'complete'
        )
        if user_content == message:
            messages.append(ModelMessage(role='user', content=message))
        else:
            messages.append(ModelMessage(role='user', content=user_content))
        return messages

    async def _generate(
        self,
        *,
        session: AiChatSession,
        run: AiChatRun,
        request: PlatformChatRequestModel,
        runtime_stream: ModelServiceStreamResult,
        lock: Any,
    ) -> AsyncIterator[PlatformChatEvent]:
        del request
        state = _GenerationState(content_parts=[], reasoning_parts=[])
        try:
            await self._start_run(run)
            yield PlatformChatEvent(
                'meta',
                _freeze_data({'sessionId': session.session_id, 'runId': run.run_id, 'bindingId': run.binding_id}),
            )
            async for event in self._forward_events(session, run, runtime_stream, state):
                yield event
            yield self._status_event(session.session_id, run.run_id, 'completed')
        except (ModelServiceError, ChatCoordinationError) as error:
            state.final_status = 'failed'
            state.error_code = error.code
            state.error_message = error.public_message
            yield self._error_event(session.session_id, run.run_id, state.error_code, state.error_message)
            yield self._status_event(session.session_id, run.run_id, state.final_status)
        except asyncio.CancelledError:
            state.final_status = 'cancelled'
            state.active_error = sys.exc_info()[1]
            raise
        except GeneratorExit:
            state.final_status = 'interrupted'
            state.active_error = sys.exc_info()[1]
            raise
        except Exception:
            state.final_status = 'failed'
            state.error_code = 'MODEL_RUNTIME_ERROR'
            state.error_message = '模型运行时调用失败'
            yield self._error_event(session.session_id, run.run_id, state.error_code, state.error_message)
            yield self._status_event(session.session_id, run.run_id, state.final_status)
        finally:
            try:
                await runtime_stream.aclose()
            except BaseException:
                if state.active_error is None:
                    state.final_status = 'failed'
                    state.error_code = 'MODEL_RUNTIME_ERROR'
                    state.error_message = '模型运行时调用失败'
            await self._persist_terminal(
                session=session,
                run=run,
                status=state.final_status,
                content=''.join(state.content_parts),
                reasoning=''.join(state.reasoning_parts) or None,
                usage=state.usage,
                error_code=state.error_code,
                error_message=state.error_message,
            )
            try:
                await lock.__aexit__(type(state.active_error) if state.active_error else None, state.active_error, None)
            except BaseException:
                if state.active_error is None:
                    raise

    async def _start_run(self, run: AiChatRun) -> None:
        await self._dao.transition_run_status(
            self._db,
            run.run_id,
            expected_statuses={'pending'},
            values={'status': 'running', 'started_at': self._clock(), 'updated_at': self._clock()},
        )
        await self._commit()

    async def _forward_events(
        self,
        session: AiChatSession,
        run: AiChatRun,
        runtime_stream: ModelServiceStreamResult,
        state: _GenerationState,
    ) -> AsyncIterator[PlatformChatEvent]:
        async for event in runtime_stream:
            if await self._coordinator.is_cancelled(run.run_id):
                raise asyncio.CancelledError
            normalized = self._platform_event_for_runtime(session.session_id, run.run_id, event, state)
            if normalized is not None:
                yield normalized

    @staticmethod
    def _platform_event_for_runtime(
        session_id: str,
        run_id: str,
        event: ModelStreamEvent,
        state: _GenerationState,
    ) -> PlatformChatEvent | None:
        if event.event_type == 'content' and event.delta:
            state.content_parts.append(event.delta)
            return PlatformChatEvent(
                'content', _freeze_data({'sessionId': session_id, 'runId': run_id, 'delta': event.delta})
            )
        if event.event_type == 'reasoning' and event.delta:
            state.reasoning_parts.append(event.delta)
            return PlatformChatEvent(
                'reasoning', _freeze_data({'sessionId': session_id, 'runId': run_id, 'delta': event.delta})
            )
        if event.event_type == 'usage' and event.usage is not None:
            state.usage = event.usage
            return PlatformChatEvent(
                'usage',
                _freeze_data(
                    {
                        'sessionId': session_id,
                        'runId': run_id,
                        'inputTokens': event.usage.input_tokens,
                        'outputTokens': event.usage.output_tokens,
                    }
                ),
            )
        return None

    @staticmethod
    def _error_event(session_id: str, run_id: str, code: str | None, message: str | None) -> PlatformChatEvent:
        return PlatformChatEvent(
            'error', _freeze_data({'sessionId': session_id, 'runId': run_id, 'code': code, 'message': message})
        )

    @staticmethod
    def _status_event(session_id: str, run_id: str, status: str) -> PlatformChatEvent:
        return PlatformChatEvent('done', _freeze_data({'sessionId': session_id, 'runId': run_id, 'status': status}))

    async def _persist_terminal(
        self,
        *,
        session: AiChatSession,
        run: AiChatRun,
        status: str,
        content: str,
        reasoning: str | None,
        usage: Any,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        if content or reasoning:
            await self._dao.create_message(
                self._db,
                AiChatMessage(
                    message_id=self._id_factory('msg'),
                    session_id=session.session_id,
                    client_message_id=f'{run.run_id}:assistant',
                    role='assistant',
                    content=content,
                    reasoning_content=reasoning,
                    images=[],
                    status='complete'
                    if status == 'completed'
                    else ('cancelled' if status == 'cancelled' else 'interrupted'),
                    created_at=self._clock(),
                    updated_at=self._clock(),
                ),
            )
        values: dict[str, object] = {
            'status': status,
            'finished_at': self._clock(),
            'updated_at': self._clock(),
            'error_code': error_code,
            'error_message': error_message,
        }
        if usage is not None:
            values.update(
                {
                    'input_tokens': usage.input_tokens,
                    'output_tokens': usage.output_tokens,
                    'reasoning_tokens': usage.reasoning_tokens,
                    'cache_read_tokens': usage.cache_read_tokens,
                    'cache_write_tokens': usage.cache_write_tokens,
                    'raw_cost': usage.raw_cost,
                }
            )
        await self._dao.transition_run_status(
            self._db,
            run.run_id,
            expected_statuses={'pending', 'running'},
            values=values,
        )
        await self._commit()

    async def _finish_setup_failure(self, run_id: str, error: BaseException) -> None:
        if isinstance(error, (ChatCoordinationError, ModelServiceError)):
            code, message = error.code, error.public_message
        elif isinstance(error, asyncio.CancelledError):
            code, message = 'CHAT_CANCELLED', '对话已取消'
        else:
            code, message = 'MODEL_RUNTIME_ERROR', '模型运行时调用失败'
        await self._dao.transition_run_status(
            self._db,
            run_id,
            expected_statuses={'pending'},
            values={
                'status': 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed',
                'error_code': code,
                'error_message': message,
                'finished_at': self._clock(),
                'updated_at': self._clock(),
            },
        )
        await self._commit()

    async def _commit(self) -> None:
        await self._db.commit()

    async def _rollback_if_active(self) -> None:
        if getattr(self._db, 'in_transaction', lambda: False)():
            await self._db.rollback()

    async def _rollback_preserving_error(self) -> None:
        if not getattr(self._db, 'in_transaction', lambda: False)():
            return
        try:
            await self._db.rollback()
        except BaseException:
            pass

    async def _replay(self, session_id: str, run_id: str) -> AsyncIterator[PlatformChatEvent]:
        yield PlatformChatEvent('meta', _freeze_data({'sessionId': session_id, 'runId': run_id}))
        yield PlatformChatEvent('done', _freeze_data({'sessionId': session_id, 'runId': run_id, 'status': 'completed'}))
