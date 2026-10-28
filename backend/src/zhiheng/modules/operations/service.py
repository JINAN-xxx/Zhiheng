import re
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select, delete, false, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from zhiheng.modules.operations.contracts import (
    ConfigInfo,
    CreateConfig,
    CreateDictionary,
    CreateDictionaryItem,
    CreateNotice,
    DictionaryInfo,
    DictionaryItemInfo,
    LogInfo,
    LogKind,
    LogOutcome,
    NoticeInfo,
    NoticeKind,
    NoticeStatus,
    Page,
    Patch,
    PatchConfig,
    PatchDictionary,
    PatchDictionaryItem,
    PatchNotice,
)
from zhiheng.modules.operations.orm import Config, Dictionary, DictionaryItem, Notice, OperationLog, Versioned
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

Entity = TypeVar('Entity', bound=Versioned)
Info = TypeVar('Info', bound=BaseModel)
_SENSITIVE = re.compile(
    r'password|passwd|secret|token|cookie|authorization|api[ _-]?key|密钥|密码|令牌|://|[\{\[]', re.I
)
_CODE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_.:-]{0,79}$')
MAX_SEARCH_BYTES = 2000
MAX_PAGE_SIZE = 100


class OperationsService:
    """Transactions belong to callers; every successful administration write adds its audit row."""

    @staticmethod
    def _permission(principal: Principal, area: str, write: bool = False) -> None:
        principal.require_permission(f'system:{area}:{"write" if write else "read"}')

    @staticmethod
    def _pattern(search: str) -> str:
        try:
            encoded = search.encode('utf-8')
        except UnicodeError:
            raise AppError('invalid_operations_search', '搜索条件无效', 422) from None
        if '\x00' in search or len(encoded) > MAX_SEARCH_BYTES:
            raise AppError('invalid_operations_search', '搜索条件无效', 422)
        return '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'

    @staticmethod
    async def _page(
        session: AsyncSession,
        query: Select[tuple[Entity]],
        info: type[Info],
        *,
        page: int,
        page_size: int,
    ) -> Page[Info]:
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise AppError('invalid_operations_page', '分页参数无效', 422)
        total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        rows = await session.scalars(query.offset((page - 1) * page_size).limit(page_size))
        return Page(items=[info.model_validate(row) for row in rows], total=total or 0, page=page, page_size=page_size)

    @staticmethod
    async def _row(
        session: AsyncSession,
        model: type[Entity],
        identifier: UUID,
        *conditions: ColumnElement[bool],
        lock: bool = False,
    ) -> Entity:
        query = select(model).where(model.id == identifier, *conditions).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query)
        if row is None:
            raise AppError('operations_not_found', '记录不存在', 404)
        return row

    async def _insert(
        self,
        session: AsyncSession,
        principal: Principal,
        model: type[Entity],
        fields: dict[str, object],
        resource: str,
    ) -> Entity:
        row = model()
        for key, value in fields.items():
            setattr(row, key, value)
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            raise AppError('operations_duplicate', '编码或值已存在', 409) from None
        await self.record(
            session, principal=principal, action=f'{resource}.create', resource_type=resource, resource_id=str(row.id)
        )
        return row

    async def _update(
        self,
        session: AsyncSession,
        principal: Principal,
        model: type[Entity],
        identifier: UUID,
        body: Patch,
        resource: str,
        *conditions: ColumnElement[bool],
    ) -> Entity:
        changes = body.model_dump(exclude_unset=True, exclude={'expected_version'})
        try:
            async with session.begin_nested():
                changed = await session.scalar(
                    update(model)
                    .where(model.id == identifier, model.version == body.expected_version, *conditions)
                    .values(**changes, version=body.expected_version + 1, updated_at=utcnow())
                    .returning(model.id)
                )
        except IntegrityError:
            raise AppError('operations_duplicate', '编码或值已存在', 409) from None
        row = await self._row(session, model, identifier, *conditions)
        if changed is None:
            raise AppError('operations_version_conflict', '记录已更新，请刷新后重试', 409)
        await self.record(
            session,
            principal=principal,
            action=f'{resource}.update',
            resource_type=resource,
            resource_id=str(identifier),
        )
        return row

    async def _delete(
        self,
        session: AsyncSession,
        principal: Principal,
        model: type[Entity],
        identifier: UUID,
        expected_version: int,
        resource: str,
        *conditions: ColumnElement[bool],
    ) -> None:
        if isinstance(expected_version, bool) or expected_version < 1:
            raise AppError('invalid_operations_version', '版本参数无效', 422)
        try:
            async with session.begin_nested():
                changed = await session.scalar(
                    delete(model)
                    .where(model.id == identifier, model.version == expected_version, *conditions)
                    .returning(model.id)
                )
        except IntegrityError:
            raise AppError('operations_in_use', '记录仍有依赖数据，不能删除', 409) from None
        if changed is None:
            await self._row(session, model, identifier, *conditions)
            raise AppError('operations_version_conflict', '记录已更新，请刷新后重试', 409)
        await self.record(
            session,
            principal=principal,
            action=f'{resource}.delete',
            resource_type=resource,
            resource_id=str(identifier),
        )

    async def list_dictionaries(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        search: str = '',
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[DictionaryInfo]:
        self._permission(principal, 'dictionaries')
        pattern = self._pattern(search)
        query = select(Dictionary).where(
            or_(Dictionary.code.ilike(pattern, escape='\\'), Dictionary.name.ilike(pattern, escape='\\'))
        )
        if is_active is not None:
            query = query.where(Dictionary.is_active == is_active)
        return await self._page(
            session,
            query.order_by(Dictionary.created_at.desc(), Dictionary.id),
            DictionaryInfo,
            page=page,
            page_size=page_size,
        )

    async def get_dictionary(self, session: AsyncSession, principal: Principal, dictionary_id: UUID) -> DictionaryInfo:
        self._permission(principal, 'dictionaries')
        return DictionaryInfo.model_validate(await self._row(session, Dictionary, dictionary_id))

    async def create_dictionary(
        self, session: AsyncSession, principal: Principal, body: CreateDictionary
    ) -> DictionaryInfo:
        self._permission(principal, 'dictionaries', True)
        return DictionaryInfo.model_validate(
            await self._insert(session, principal, Dictionary, body.model_dump(), 'dictionary')
        )

    async def update_dictionary(
        self, session: AsyncSession, principal: Principal, dictionary_id: UUID, body: PatchDictionary
    ) -> DictionaryInfo:
        self._permission(principal, 'dictionaries', True)
        return DictionaryInfo.model_validate(
            await self._update(session, principal, Dictionary, dictionary_id, body, 'dictionary')
        )

    async def delete_dictionary(
        self, session: AsyncSession, principal: Principal, dictionary_id: UUID, *, expected_version: int
    ) -> None:
        self._permission(principal, 'dictionaries', True)
        row = await self._row(session, Dictionary, dictionary_id, lock=True)
        if row.version != expected_version:
            raise AppError('operations_version_conflict', '记录已更新，请刷新后重试', 409)
        if await session.scalar(
            select(DictionaryItem.id).where(DictionaryItem.dictionary_id == dictionary_id).limit(1)
        ):
            raise AppError('operations_in_use', '字典仍有数据，不能删除', 409)
        await self._delete(session, principal, Dictionary, dictionary_id, expected_version, 'dictionary')

    async def list_dictionary_items(
        self,
        session: AsyncSession,
        principal: Principal,
        dictionary_id: UUID,
        *,
        search: str = '',
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[DictionaryItemInfo]:
        self._permission(principal, 'dictionaries')
        await self._row(session, Dictionary, dictionary_id)
        pattern = self._pattern(search)
        query = select(DictionaryItem).where(
            DictionaryItem.dictionary_id == dictionary_id,
            or_(DictionaryItem.label.ilike(pattern, escape='\\'), DictionaryItem.value.ilike(pattern, escape='\\')),
        )
        if is_active is not None:
            query = query.where(DictionaryItem.is_active == is_active)
        return await self._page(
            session,
            query.order_by(DictionaryItem.sort_order, DictionaryItem.id),
            DictionaryItemInfo,
            page=page,
            page_size=page_size,
        )

    async def get_dictionary_item(
        self, session: AsyncSession, principal: Principal, dictionary_id: UUID, item_id: UUID
    ) -> DictionaryItemInfo:
        self._permission(principal, 'dictionaries')
        return DictionaryItemInfo.model_validate(
            await self._row(session, DictionaryItem, item_id, DictionaryItem.dictionary_id == dictionary_id)
        )

    async def create_dictionary_item(
        self, session: AsyncSession, principal: Principal, dictionary_id: UUID, body: CreateDictionaryItem
    ) -> DictionaryItemInfo:
        self._permission(principal, 'dictionaries', True)
        # The same parent lock is held by deletion, including when its item set is empty.
        await self._row(session, Dictionary, dictionary_id, lock=True)
        return DictionaryItemInfo.model_validate(
            await self._insert(
                session,
                principal,
                DictionaryItem,
                {**body.model_dump(), 'dictionary_id': dictionary_id},
                'dictionary_item',
            )
        )

    async def update_dictionary_item(
        self, session: AsyncSession, principal: Principal, dictionary_id: UUID, item_id: UUID, body: PatchDictionaryItem
    ) -> DictionaryItemInfo:
        self._permission(principal, 'dictionaries', True)
        return DictionaryItemInfo.model_validate(
            await self._update(
                session,
                principal,
                DictionaryItem,
                item_id,
                body,
                'dictionary_item',
                DictionaryItem.dictionary_id == dictionary_id,
            )
        )

    async def delete_dictionary_item(
        self, session: AsyncSession, principal: Principal, dictionary_id: UUID, item_id: UUID, *, expected_version: int
    ) -> None:
        self._permission(principal, 'dictionaries', True)
        await self._delete(
            session,
            principal,
            DictionaryItem,
            item_id,
            expected_version,
            'dictionary_item',
            DictionaryItem.dictionary_id == dictionary_id,
        )

    @staticmethod
    def _config(key: str, value: str) -> None:
        # Only parameters with implemented public behavior are maintainable here.
        if key != 'console.notice_enabled':
            raise AppError('operations_config_key', '仅支持已接入的公开业务参数 console.notice_enabled', 422)
        if value not in ('true', 'false'):
            raise AppError('operations_config_value', '公告开关只接受 true 或 false', 422)

    async def list_configs(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        search: str = '',
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ConfigInfo]:
        self._permission(principal, 'configs')
        pattern = self._pattern(search)
        query = select(Config).where(
            Config.key == 'console.notice_enabled',
            or_(Config.key.ilike(pattern, escape='\\'), Config.name.ilike(pattern, escape='\\')),
        )
        if is_active is not None:
            query = query.where(Config.is_active == is_active)
        return await self._page(
            session, query.order_by(Config.created_at.desc(), Config.id), ConfigInfo, page=page, page_size=page_size
        )

    async def get_config(self, session: AsyncSession, principal: Principal, config_id: UUID) -> ConfigInfo:
        self._permission(principal, 'configs')
        row = await self._row(session, Config, config_id, Config.key == 'console.notice_enabled')
        self._config(row.key, row.value)
        return ConfigInfo.model_validate(row)

    async def create_config(self, session: AsyncSession, principal: Principal, body: CreateConfig) -> ConfigInfo:
        self._permission(principal, 'configs', True)
        self._config(body.key, body.value)
        return ConfigInfo.model_validate(await self._insert(session, principal, Config, body.model_dump(), 'config'))

    async def update_config(
        self, session: AsyncSession, principal: Principal, config_id: UUID, body: PatchConfig
    ) -> ConfigInfo:
        self._permission(principal, 'configs', True)
        row = await self._row(session, Config, config_id, Config.key == 'console.notice_enabled')
        self._config(body.key if body.key is not None else row.key, body.value if body.value is not None else row.value)
        return ConfigInfo.model_validate(await self._update(session, principal, Config, config_id, body, 'config'))

    async def delete_config(
        self, session: AsyncSession, principal: Principal, config_id: UUID, *, expected_version: int
    ) -> None:
        self._permission(principal, 'configs', True)
        await self._delete(
            session, principal, Config, config_id, expected_version, 'config', Config.key == 'console.notice_enabled'
        )

    async def list_notices(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        search: str = '',
        status: NoticeStatus | None = None,
        kind: NoticeKind | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[NoticeInfo]:
        self._permission(principal, 'notices')
        pattern = self._pattern(search)
        query = select(Notice).where(Notice.title.ilike(pattern, escape='\\'))
        if status is not None:
            query = query.where(Notice.status == status)
        if kind is not None:
            query = query.where(Notice.kind == kind)
        return await self._page(
            session, query.order_by(Notice.created_at.desc(), Notice.id), NoticeInfo, page=page, page_size=page_size
        )

    async def get_notice(self, session: AsyncSession, principal: Principal, notice_id: UUID) -> NoticeInfo:
        self._permission(principal, 'notices')
        return NoticeInfo.model_validate(await self._row(session, Notice, notice_id))

    async def create_notice(self, session: AsyncSession, principal: Principal, body: CreateNotice) -> NoticeInfo:
        self._permission(principal, 'notices', True)
        return NoticeInfo.model_validate(await self._insert(session, principal, Notice, body.model_dump(), 'notice'))

    async def update_notice(
        self, session: AsyncSession, principal: Principal, notice_id: UUID, body: PatchNotice
    ) -> NoticeInfo:
        self._permission(principal, 'notices', True)
        return NoticeInfo.model_validate(await self._update(session, principal, Notice, notice_id, body, 'notice'))

    async def delete_notice(
        self, session: AsyncSession, principal: Principal, notice_id: UUID, *, expected_version: int
    ) -> None:
        self._permission(principal, 'notices', True)
        await self._delete(session, principal, Notice, notice_id, expected_version, 'notice')

    async def published_notices(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[NoticeInfo]:
        # Authentication is supplied by the route. All authenticated users can read published text.
        enabled = await session.scalar(
            select(Config.value).where(Config.key == 'console.notice_enabled', Config.is_active.is_(True))
        )
        query = select(Notice).where(Notice.status == 'published')
        if enabled is not None and enabled != 'true':
            query = query.where(false())
        return await self._page(
            session, query.order_by(Notice.created_at.desc(), Notice.id), NoticeInfo, page=page, page_size=page_size
        )

    async def record(
        self,
        session: AsyncSession,
        *,
        principal: Principal | None,
        action: str,
        resource_type: str = '',
        resource_id: str | None = None,
        outcome: LogOutcome = 'success',
        message: str = '',
        kind: LogKind = 'operation',
        username: str = '',
    ) -> None:
        """Record only a structured summary: never pass request bodies, URLs, credentials or exceptions."""
        if kind not in ('login', 'operation') or outcome not in ('success', 'failed'):
            raise ValueError('日志类型或结果无效')
        action = action if _CODE.fullmatch(action) else kind
        resource_type = resource_type if _CODE.fullmatch(resource_type) else ''
        if resource_id is not None:
            try:
                resource_id = str(UUID(resource_id))
            except (ValueError, AttributeError):
                resource_id = None
        username = principal.username if principal is not None else username
        if re.fullmatch(r'[a-zA-Z0-9_.@-]{3,80}', username) is None:
            username = '[无效账号]'
        if _SENSITIVE.search(message):
            message = '已省略敏感内容'
        message = ''.join(character for character in message if character.isprintable())[:500]
        session.add(
            OperationLog(
                kind=kind,
                action=action,
                username=username,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                message=message,
            )
        )
        await session.flush()

    async def list_logs(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        kind: LogKind | None = None,
        outcome: LogOutcome | None = None,
        search: str = '',
        page: int = 1,
        page_size: int = 20,
    ) -> Page[LogInfo]:
        self._permission(principal, 'logs')
        pattern = self._pattern(search)
        query = select(OperationLog).where(
            or_(
                OperationLog.username.ilike(pattern, escape='\\'),
                OperationLog.action.ilike(pattern, escape='\\'),
                OperationLog.resource_type.ilike(pattern, escape='\\'),
                OperationLog.message.ilike(pattern, escape='\\'),
            )
        )
        if kind is not None:
            query = query.where(OperationLog.kind == kind)
        if outcome is not None:
            query = query.where(OperationLog.outcome == outcome)
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise AppError('invalid_operations_page', '分页参数无效', 422)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(OperationLog.created_at.desc(), OperationLog.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return Page(
            items=[LogInfo.model_validate(row) for row in rows], total=total or 0, page=page, page_size=page_size
        )
