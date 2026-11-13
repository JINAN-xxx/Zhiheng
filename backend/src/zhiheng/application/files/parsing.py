"""Finite, recoverable parsing ticks. HTTP waits never occupy knowledge build slots."""

import asyncio
import hashlib
import json
import logging
import re
from time import monotonic
from typing import TYPE_CHECKING
from uuid import UUID

from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileService, PreparedAttachment
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import (
    PARSER_VERSION,
    DocumentSourceValidator,
    MinerUClientFactory,
    MinerUClientPort,
    MinerUFailure,
    MinerUResultDecoder,
    ParsingClaim,
    ParsingPending,
    ParsingServiceStatus,
    ParsingSettingsService,
    ParsingStatusInfo,
    ParsingStore,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from zhiheng.application.files.inputs import FileInputService

logger = logging.getLogger(__name__)
MAX_UPSTREAM_ID = 256
REMOTE_FORMATS = frozenset({'pdf', 'docx', 'xlsx', 'png', 'jpg', 'jpeg', 'webp'})
DOCUMENT_ERRORS = {
    'encrypted_document': '文档已加密，请上传无密码副本',
    'invalid_document': '文档损坏或格式无效',
    'document_too_large': '文档展开内容超过限制',
}
REMOTE_RESULT_ERRORS = frozenset({'parse_task_unavailable', 'parse_result_invalid'})


class DocumentParsing:
    def __init__(
        self,
        database: Database,
        files: FileService,
        inputs: 'FileInputService',
        identity: IdentityService,
        settings: ParsingSettingsService,
        client_factory: MinerUClientFactory,
        validate_source: DocumentSourceValidator,
        decode_result: MinerUResultDecoder,
    ) -> None:
        self.database, self.files, self.inputs, self.identity, self.settings = (
            database,
            files,
            inputs,
            identity,
            settings,
        )
        self.client_factory, self.validate_source, self.decode_result = client_factory, validate_source, decode_result
        self.store = ParsingStore()
        self._health_lock = asyncio.Lock()
        self._health_cache: tuple[str, float, bool] | None = None
        self.authorize_origin: Callable[[UUID], Awaitable[None]] | None = None

    async def prepare(self, principal: Principal, file_id: UUID, parent_id: UUID) -> PreparedAttachment:
        async with self.database.sessions.begin() as session:
            info = await self.files.get(session, principal, file_id)
            config, _ = await self.settings.snapshot(session)
            extension = info.original_filename.rsplit('.', 1)[-1].lower()
            if extension not in REMOTE_FORMATS or not (
                config.get('local_url') or (config.get('cloud_enabled') and config.get('cloud_url'))
            ):
                direct = True
                phase, artifact, code, message = '', None, None, None
            else:
                direct = False
                phase, artifact, code, message = await self.store.ensure(
                    session,
                    parent_id=parent_id,
                    owner_id=principal.id,
                    file_id=file_id,
                    sha256=info.sha256,
                    version=info.version,
                    acl_version=info.acl_version,
                    config=config,
                )
        if direct:
            return await self.inputs.prepare(principal, file_id)
        if phase == 'completed' and artifact is not None:
            return await self.inputs.from_artifact(principal, file_id, artifact)
        if phase in {'failed', 'submission_unknown', 'cancelled'}:
            raise AppError(code or 'parse_failed', message or '文档解析未完成，原文件已保留', 422)
        raise ParsingPending

    async def status(self, principal: Principal) -> ParsingStatusInfo:
        principal.require_permission('system:configs:read')
        async with self.database.sessions() as session:
            config, _ = await self.settings.snapshot(session)
        url = config.get('local_url')
        local = (
            await self.client_factory(url, self.settings.key(config, 'local')).health()
            if isinstance(url, str)
            else ParsingServiceStatus(configured=False, available=False)
        )
        cloud_url = config.get('cloud_url')
        cloud = (
            await self.client_factory(cloud_url, self.settings.key(config, 'cloud')).health()
            if config.get('cloud_enabled') is True and isinstance(cloud_url, str)
            else ParsingServiceStatus(configured=isinstance(cloud_url, str), available=False)
        )
        return ParsingStatusInfo(local=local, cloud=cloud, cloud_enabled=config.get('cloud_enabled') is True)

    async def available(self) -> bool:
        async with self.database.sessions() as session:
            config, version = await self.settings.snapshot(session)
        targets = [('local', config.get('local_url'))]
        if config.get('cloud_enabled') is True:
            targets.append(('cloud', config.get('cloud_url')))
        cache_key = str(version)
        async with self._health_lock:
            cached = self._health_cache
            if cached is not None and cached[0] == cache_key and cached[1] > monotonic():
                return cached[2]
            available = False
            for engine, url in targets:
                if (
                    isinstance(url, str)
                    and (await self.client_factory(url, self.settings.key(config, engine)).health()).available
                ):
                    available = True
                    break
            self._health_cache = cache_key, monotonic() + 15, available
            return available

    async def _update(
        self,
        claim: ParsingClaim,
        *,
        phase: str,
        delay: float = 0,
        task_id: str | None = None,
        upstream_file_id: str | None = None,
        engine_version: str | None = None,
        parser_version: str | None = None,
        artifact_id: UUID | None = None,
        upload_target: dict[str, object] | None = None,
        error: AppError | None = None,
    ) -> None:
        async with self.database.sessions.begin() as session:
            await self.store.update(
                session,
                claim,
                phase=phase,
                delay=delay,
                task_id=task_id,
                upstream_file_id=upstream_file_id,
                engine_version=engine_version,
                parser_version=parser_version,
                artifact_id=artifact_id,
                upload_target=upload_target,
                error=error,
            )

    def _client(self, claim: ParsingClaim) -> MinerUClientPort:
        url = claim.config.get(claim.engine + '_url')
        if not isinstance(url, str):
            raise MinerUFailure('parse_service_unconfigured', recoverable=True)
        return self.client_factory(url, self.settings.key(claim.config, claim.engine))

    async def _fallback(self, claim: ParsingClaim, code: str) -> None:
        logger.info('Document parser fallback parse_id=%s engine=%s reason=%s', claim.id, claim.engine, code)
        async with self.database.sessions.begin() as session:
            await self.store.fallback(session, claim, code)

    async def _cancel(self, claim: ParsingClaim) -> None:
        if claim.task_id and claim.engine != 'native':
            try:
                await self._client(claim).cancel(claim.task_id)
            except AppError:
                logger.warning('Document parse cancellation unconfirmed parse_id=%s engine=%s', claim.id, claim.engine)

    async def _expire(self, claim: ParsingClaim) -> None:
        await self._cancel(claim)
        if claim.engine != 'native':
            await self._fallback(claim, 'parse_timeout')
        else:
            await self._update(
                claim,
                phase='failed',
                error=AppError('parse_timeout', '文档解析超时，原文件已保留，可稍后重试', 422),
            )

    async def tick(self) -> bool:  # noqa: PLR0912 - one fenced tick owns failure and cancellation transitions
        async with self.database.sessions.begin() as session:
            claim = await self.store.claim(session)
        if claim is None:
            return False
        try:
            if self.authorize_origin is not None:
                await self.authorize_origin(claim.parent_id)
            async with self.database.sessions.begin() as session:
                await self.store.require_parent(session, claim.parent_id, claim.owner_id, claim.file_id)
                principal = await self.identity.principal_for_user(session, claim.owner_id)
                info = await self.files.get(session, principal, claim.file_id)
            if (
                info.sha256 != claim.source_sha256
                or info.version != claim.source_version
                or info.acl_version != claim.source_acl_version
            ):
                raise AppError('file_snapshot_changed', '文件或访问权限已变化，请重新提交', 409)
            if claim.deadline <= utcnow():
                await self._expire(claim)
            elif claim.engine == 'native':
                item = await self.inputs.prepare(principal, claim.file_id)
                await self._complete(claim, item.summary.artifact_file_id, PARSER_VERSION)
            elif claim.phase == 'selecting':
                await self._select(claim, principal)
            else:
                await self._poll(claim, principal)
        except MinerUFailure as error:
            if error.unknown:
                await self._update(
                    claim,
                    phase='submission_unknown',
                    error=AppError(
                        'parse_submission_unknown', '解析提交结果待确认，原文件已保留，请联系管理员核对原任务', 503
                    ),
                )
            elif error.recoverable and claim.task_id is None:
                await self._fallback(claim, error.code)
            elif error.code in {'parse_service_wait', 'parse_download_wait', 'parse_upload_wait'} or (
                error.recoverable and claim.task_id is not None
            ):
                # Poll the same known task; a failed transfer must never repeat task creation.
                await self._update(claim, phase='polling', delay=3)
            else:
                await self._cancel(claim)
                await self._fallback(claim, error.code)
        except AppError as error:
            if error.code == 'parse_lease_lost':
                return True
            if claim.engine != 'native' and error.code in REMOTE_RESULT_ERRORS:
                await self._fallback(claim, error.code)
                return True
            await self._cancel(claim)
            cancelled = error.code == 'knowledge_cancelled' or error.status_code in {401, 403, 404}
            if error.code == 'ocr_required' and claim.engine == 'native':
                error = AppError('ocr_required', '当前 OCR 服务不可用，原文件已保留，可稍后重新解析', 422)
            await self._update(claim, phase='cancelled' if cancelled else 'failed', error=error)
        except asyncio.CancelledError:
            # Leave the durable phase and lease for restart recovery; do not submit a replacement task.
            raise
        except Exception as error:
            logger.error(
                'Document parse failed parse_id=%s engine=%s error_type=%s',
                claim.id,
                claim.engine,
                type(error).__name__,
            )
            await self._cancel(claim)
            await self._update(claim, phase='failed', error=AppError('parse_failed', '文档解析失败，原文件已保留', 422))
        return True

    async def _complete(self, claim: ParsingClaim, artifact: UUID | None, parser_version: str) -> None:
        if artifact is None:
            raise AppError('parse_result_invalid', '解析产物未保存', 503)
        if self.authorize_origin is not None:
            await self.authorize_origin(claim.parent_id)
        async with self.database.sessions.begin() as session:
            await self.store.require_parent(session, claim.parent_id, claim.owner_id, claim.file_id)
            await self.store.update(
                session, claim, phase='completed', artifact_id=artifact, parser_version=parser_version
            )

    async def _capacity(self, claim: ParsingClaim, *, reserve: bool) -> bool:
        async with self.database.sessions.begin() as session:
            available = await self.store.local_slot(session, claim)
            if available and reserve:
                await self.store.creating(session, claim)
        if available:
            return True
        waited = (utcnow() - claim.created_at).total_seconds()
        if waited >= int(str(claim.config['queue_timeout_seconds'])):
            await self._fallback(claim, 'parse_queue_timeout')
        else:
            await self._update(claim, phase='selecting', delay=2)
        return False

    async def _select(self, claim: ParsingClaim, principal: Principal) -> None:
        if claim.engine == 'cloud' and claim.config.get('cloud_enabled') is not True:
            await self._fallback(claim, 'cloud_disabled')
            return
        client = self._client(claim)
        if claim.engine == 'local' and not await self._capacity(claim, reserve=False):
            return
        health = await client.health()
        if not health.available:
            await self._fallback(claim, 'parse_service_unavailable')
            return
        version = re.sub(r'[^A-Za-z0-9._-]', '', health.version or 'unknown')[:40] or 'unknown'
        tier = str(claim.config.get('tier', 'standard'))
        signature = hashlib.sha256(
            json.dumps([version, tier, 'markdown-v2', client.base, claim.config.get('policy_revision', 1)]).encode()
        ).hexdigest()[:12]
        parser_version = f'mineru-{version}-{signature}'[:64]
        async with self.database.sessions.begin() as session:
            cached = await self.files.cached_artifact(session, principal, claim.file_id, parser_version)
        if cached is not None:
            await self._complete(claim, cached[0], parser_version)
            return
        async with self.database.sessions() as session:
            source, content = await self.files.read(session, principal, claim.file_id)
        if (
            source.sha256 != claim.source_sha256
            or source.version != claim.source_version
            or source.acl_version != claim.source_acl_version
        ):
            raise AppError('file_snapshot_changed', '文件或访问权限已变化，请重新提交', 409)
        await self.validate_source(content, filename=source.original_filename)
        if claim.engine == 'local':
            if not await self._capacity(claim, reserve=True):
                return
        else:
            async with self.database.sessions.begin() as session:
                await self.store.creating(session, claim)
        async with self.database.sessions() as session:
            info = await self.files.get(session, principal, claim.file_id)
        filename = info.original_filename.replace('\\', '/').rsplit('/', 1)[-1]
        filename = re.sub(r'[\x00-\x1f\x7f:]', '_', filename)[:1000] or 'document.pdf'
        task = await client.create(filename, info.size, claim.source_sha256, tier)
        task_id = task.get('task_id')
        files = task.get('files')
        if (
            not isinstance(task_id, str)
            or not task_id
            or len(task_id) > MAX_UPSTREAM_ID
            or not isinstance(files, list)
            or len(files) != 1
            or not isinstance(files[0], dict)
        ):
            raise MinerUFailure('parse_submission_unknown', unknown=True)
        file_id = files[0].get('file_id')
        if not isinstance(file_id, str) or not file_id or len(file_id) > MAX_UPSTREAM_ID:
            raise MinerUFailure('parse_submission_unknown', unknown=True)
        upload_target = files[0].get('upload')
        await asyncio.shield(
            self._update(
                claim,
                phase='uploading',
                task_id=task_id,
                upstream_file_id=file_id,
                upload_target=upload_target if isinstance(upload_target, dict) else None,
                engine_version=version,
                parser_version=parser_version,
            )
        )

    async def _poll(self, claim: ParsingClaim, principal: Principal) -> None:
        if claim.task_id is None:
            raise MinerUFailure('parse_submission_unknown', unknown=True)
        client = self._client(claim)
        task = await client.get(claim.task_id)
        entries = task.get('files')
        if task.get('task_id') != claim.task_id or not isinstance(entries, list):
            raise MinerUFailure('parse_service_response')
        entry = next(
            (item for item in entries if isinstance(item, dict) and item.get('file_id') == claim.upstream_file_id), None
        )
        if entry is None:
            raise MinerUFailure('parse_service_response')
        status = entry.get('status')
        upload = entry.get('upload') or claim.config.get('upload_target')
        if status == 'completed':
            package = await client.download(entry.get('result_url'))
            details = entry.get('parse')
            actual_tier = details.get('tier') if isinstance(details, dict) else None
            actual_version = details.get('parser_version') if isinstance(details, dict) else None
            if not isinstance(actual_version, str):
                actual_version = claim.engine_version if claim.engine == 'local' else 'unknown'
            result = self.decode_result(
                package,
                engine='mineru_' + claim.engine,
                version=(actual_version or 'unknown')[:80],
                parser_version=claim.parser_version or 'mineru-unknown',
                tier=actual_tier
                if isinstance(actual_tier, str) and actual_tier in {'flash', 'basic', 'standard', 'advanced'}
                else 'unknown',
            )
            result = result.model_copy(
                update={
                    'metadata': {
                        **result.metadata,
                        'service_version': claim.engine_version or 'unknown',
                        'requested_tier': str(claim.config['tier']),
                    }
                }
            )
            artifact = await self.inputs.store_parsed(
                principal,
                claim.file_id,
                result,
                source_sha256=claim.source_sha256,
                source_version=claim.source_version,
                source_acl_version=claim.source_acl_version,
            )
            await self._complete(claim, artifact, result.parser_version)
        elif status == 'waiting-file' and isinstance(upload, dict):
            async with self.database.sessions() as session:
                source, content = await self.files.read(session, principal, claim.file_id)
                if (
                    source.sha256 != claim.source_sha256
                    or source.version != claim.source_version
                    or source.acl_version != claim.source_acl_version
                ):
                    raise AppError('file_snapshot_changed', '文件或访问权限已变化，请重新提交', 409)
            await client.upload(upload, content)
            await self._update(claim, phase='polling', delay=1)
        elif status in {'failed', 'canceled'}:
            detail = entry.get('error')
            code = detail.get('code') if isinstance(detail, dict) else None
            if isinstance(code, str) and code in DOCUMENT_ERRORS:
                raise AppError(code, DOCUMENT_ERRORS[code], 422)
            # A definitive remote failure is eligible for the next parser even
            # when the service hides engine/OOM details behind a generic code.
            reason = code if isinstance(code, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code) else 'parse_failed'
            await self._fallback(claim, reason)
        elif status in {'waiting-file', 'pending', 'running'}:
            await self._update(claim, phase='polling', delay=2)
        else:
            raise MinerUFailure('parse_service_response')
