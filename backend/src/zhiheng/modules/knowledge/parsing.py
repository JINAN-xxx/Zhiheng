"""Parsing configuration and durable task facts; network IO belongs to the application."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.knowledge.contracts import JOB_TERMINAL
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeDocument, KnowledgeJob
from zhiheng.modules.knowledge.parsing_contracts import (
    PARSE_TERMINAL,
    ParsingClaim,
    ParsingSettingsInfo,
    ParsingSettingsPatch,
)
from zhiheng.modules.knowledge.parsing_orm import ParsingConfiguration, ParsingJob
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

DEFAULT_PARSING: dict[str, object] = {
    'local_url': None,
    'local_key': None,
    'cloud_enabled': False,
    'cloud_url': None,
    'cloud_key': None,
    'tier': 'standard',
    'timeout_seconds': 900,
    'queue_timeout_seconds': 120,
}


class ParsingSettingsService:
    def __init__(self, cipher: CredentialCipher) -> None:
        self.cipher = cipher

    async def snapshot(self, session: AsyncSession) -> tuple[dict[str, object], int]:
        row = await session.get(ParsingConfiguration, 1)
        return (
            {**DEFAULT_PARSING, **(row.config if row else {}), 'policy_revision': row.version if row else 1},
            row.version if row else 1,
        )

    def public(self, config: dict[str, object], version: int) -> ParsingSettingsInfo:
        return ParsingSettingsInfo.model_validate(
            {
                **{key: value for key, value in config.items() if key not in {'local_key', 'cloud_key'}},
                'has_local_key': bool(config.get('local_key')),
                'has_cloud_key': bool(config.get('cloud_key')),
                'version': version,
            }
        )

    async def get(self, session: AsyncSession, principal: Principal) -> ParsingSettingsInfo:
        principal.require_permission('system:configs:read')
        config, version = await self.snapshot(session)
        return self.public(config, version)

    async def update(
        self, session: AsyncSession, principal: Principal, body: ParsingSettingsPatch
    ) -> ParsingSettingsInfo:
        principal.require_permission('system:configs:write')
        await session.execute(
            insert(ParsingConfiguration).values(id=1, version=1, config=DEFAULT_PARSING).on_conflict_do_nothing()
        )
        row = await session.get(ParsingConfiguration, 1, with_for_update=True, populate_existing=True)
        assert row is not None
        if row.version != body.expected_version:
            raise AppError('parsing_settings_conflict', '解析配置已变化，请刷新后重试', 409)
        values = body.model_dump(
            exclude_unset=True,
            exclude={'expected_version', 'local_api_key', 'cloud_api_key', 'clear_local_key', 'clear_cloud_key'},
        )
        config = {**DEFAULT_PARSING, **row.config, **values}
        for prefix in ('local', 'cloud'):
            if row.config.get(prefix + '_url') != config.get(prefix + '_url'):
                config[prefix + '_key'] = None
            key = body.local_api_key if prefix == 'local' else body.cloud_api_key
            clear = body.clear_local_key if prefix == 'local' else body.clear_cloud_key
            if clear:
                config[prefix + '_key'] = None
            elif key is not None and key.get_secret_value():
                config[prefix + '_key'] = self.cipher.encrypt(key.get_secret_value())
        if config['cloud_enabled'] and not config['cloud_url']:
            raise AppError('invalid_parsing_settings', '启用云端后备前请填写云端服务地址', 422)
        row.config, row.version = config, row.version + 1
        return self.public(config, row.version)

    def key(self, config: dict[str, object], engine: str) -> str:
        value = config.get(engine + '_key')
        return self.cipher.decrypt(value) if isinstance(value, str) and value else ''


class ParsingStore:
    async def require_parent(self, session: AsyncSession, parent_id: UUID, owner_id: UUID, file_id: UUID) -> None:
        found = await session.scalar(
            select(KnowledgeJob.id)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeJob.base_id)
            .join(
                KnowledgeDocument,
                and_(KnowledgeDocument.base_id == KnowledgeBase.id, KnowledgeDocument.file_id == file_id),
            )
            .where(
                KnowledgeJob.id == parent_id,
                KnowledgeJob.status.not_in((*JOB_TERMINAL, 'cancel_requested')),
                KnowledgeBase.owner_id == owner_id,
                KnowledgeBase.deleted_at.is_(None),
                KnowledgeBase.active_job_id == parent_id,
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocument.job_id == parent_id,
            )
        )
        if found is None:
            raise AppError('knowledge_cancelled', '解析任务已失效', 409)

    async def ensure(
        self,
        session: AsyncSession,
        *,
        parent_id: UUID,
        owner_id: UUID,
        file_id: UUID,
        sha256: str,
        version: int,
        acl_version: int,
        config: dict[str, object],
    ) -> tuple[str, UUID | None, str | None, str | None]:
        await self.require_parent(session, parent_id, owner_id, file_id)
        await session.execute(
            insert(ParsingJob)
            .values(
                id=uuid4(),
                parent_id=parent_id,
                owner_id=owner_id,
                file_id=file_id,
                source_sha256=sha256,
                source_version=version,
                source_acl_version=acl_version,
                config=config,
                engine='local',
                phase='selecting',
                history=[],
                deadline=utcnow() + timedelta(seconds=int(str(config['timeout_seconds']))),
                next_poll_at=utcnow(),
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=['parent_id', 'file_id', 'source_sha256'])
        )
        row = await session.scalar(
            select(ParsingJob).where(
                ParsingJob.parent_id == parent_id, ParsingJob.file_id == file_id, ParsingJob.source_sha256 == sha256
            )
        )
        assert row is not None
        return row.phase, row.artifact_id, row.error_code, row.error_message

    async def claim(self, session: AsyncSession) -> ParsingClaim | None:
        row = await session.scalar(
            select(ParsingJob)
            .where(
                ParsingJob.phase.not_in(PARSE_TERMINAL),
                ParsingJob.next_poll_at <= utcnow(),
                or_(ParsingJob.lease_until.is_(None), ParsingJob.lease_until <= utcnow()),
            )
            .order_by(ParsingJob.next_poll_at, ParsingJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        if row.phase == 'creating' and row.task_id is None:
            row.phase, row.error_code = 'submission_unknown', 'parse_submission_unknown'
            row.error_message = '解析提交结果待确认，原文件已保留，请联系管理员核对原任务'
            row.token, row.lease_until = None, None
            return None
        row.token, row.lease_until = uuid4(), utcnow() + timedelta(seconds=60)
        return ParsingClaim(
            row.id,
            row.parent_id,
            row.owner_id,
            row.file_id,
            row.source_sha256,
            row.source_version,
            row.source_acl_version,
            dict(row.config),
            row.engine,
            row.phase,
            row.task_id,
            row.upstream_file_id,
            row.artifact_id,
            row.token,
            row.created_at,
            row.deadline,
            row.engine_version,
            row.parser_version,
        )

    async def owned(self, session: AsyncSession, claim: ParsingClaim) -> ParsingJob:
        row = await session.get(ParsingJob, claim.id, with_for_update=True, populate_existing=True)
        if row is None or row.token != claim.token or row.lease_until is None or row.lease_until <= utcnow():
            raise AppError('parse_lease_lost', '解析执行权已失效', 409)
        return row

    async def update(  # noqa: PLR0913 - persist a fenced parse result and its resumable upload target together
        self,
        session: AsyncSession,
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
        row = await self.owned(session, claim)
        row.phase, row.next_poll_at, row.updated_at = phase, utcnow() + timedelta(seconds=delay), utcnow()
        if task_id is not None:
            row.task_id = task_id
        if upstream_file_id is not None:
            row.upstream_file_id = upstream_file_id
        if engine_version is not None:
            row.engine_version = engine_version
        if parser_version is not None:
            row.parser_version = parser_version
        if artifact_id is not None:
            row.artifact_id = artifact_id
        if upload_target is not None:
            row.config = {**row.config, 'upload_target': upload_target}
        if phase in PARSE_TERMINAL:
            row.config = {key: value for key, value in row.config.items() if key != 'upload_target'}
        if error is not None:
            row.error_code, row.error_message = error.code[:64], error.message[:300]
        row.token, row.lease_until = None, None

    async def creating(self, session: AsyncSession, claim: ParsingClaim) -> None:
        row = await self.owned(session, claim)
        row.phase = 'creating'

    async def fallback(self, session: AsyncSession, claim: ParsingClaim, code: str) -> None:
        row = await self.owned(session, claim)
        row.history = [*row.history, {'engine': row.engine, 'reason': code[:64]}]
        row.config = {key: value for key, value in row.config.items() if key != 'upload_target'}
        row.engine = 'cloud' if row.engine == 'local' else 'native'
        row.phase, row.task_id, row.upstream_file_id = 'selecting', None, None
        row.engine_version, row.parser_version = None, None
        row.next_poll_at, row.updated_at = utcnow(), utcnow()
        row.token, row.lease_until = None, None

    async def local_slot(self, session: AsyncSession, claim: ParsingClaim) -> bool:
        await session.execute(text('SELECT pg_advisory_xact_lock(784247012)'))
        return not bool(
            await session.scalar(
                select(
                    exists().where(
                        ParsingJob.id != claim.id,
                        ParsingJob.engine == 'local',
                        ParsingJob.phase.in_(('creating', 'uploading', 'polling', 'downloading')),
                    )
                )
            )
        )

    async def active(self, session: AsyncSession) -> bool:
        return bool(await session.scalar(select(exists().where(ParsingJob.phase.not_in(PARSE_TERMINAL)))))
