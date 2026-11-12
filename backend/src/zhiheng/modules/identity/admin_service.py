"""Transactional administration of identity and authorization resources."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, func, or_, select, update

from zhiheng.modules.identity.admin_contracts import CREATE_SCHEMAS, PATCH_SCHEMAS, PasswordReset
from zhiheng.modules.identity.admin_orm import (
    IdentityDepartment,
    IdentityRole,
    IdentityUserRole,
)
from zhiheng.modules.identity.admin_permissions import (
    PERMISSION_CODES,
    without_retired_permissions,
)
from zhiheng.modules.identity.builtin_roles import sync_builtin_role_membership
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    import builtins
    from collections.abc import Mapping
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.identity.service import IdentityService
    from zhiheng.shared.principal import Principal

AdminRow = IdentityUser | IdentityRole | IdentityDepartment
MODELS: dict[str, type[AdminRow]] = {
    'users': IdentityUser,
    'roles': IdentityRole,
    'departments': IdentityDepartment,
}
MAX_PAGE_SIZE = 200
DTO_FIELDS = {
    'users': ('username', 'display_name', 'email', 'phone', 'department_id', 'is_active', 'is_admin', 'remark'),
    'roles': ('code', 'name', 'permissions', 'is_active', 'remark', 'system_key'),
    'departments': ('parent_id', 'name', 'sort_order', 'leader', 'phone', 'email', 'is_active'),
}


def _error(code: str, message: str, status: int = 400) -> AppError:
    return AppError(code, message, status)


def _page(items: builtins.list[dict[str, Any]], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size}


class IdentityAdminService:
    def __init__(self, identity: IdentityService) -> None:
        self.identity = identity

    @staticmethod
    def _model(kind: str) -> type[AdminRow]:
        if kind not in MODELS:
            raise _error('admin_resource_not_found', '管理资源不存在', 404)
        return MODELS[kind]

    @staticmethod
    def _pagination(page: int, page_size: int) -> None:
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise _error('invalid_pagination', '分页参数无效')

    @staticmethod
    def _validated(kind: str, fields: Mapping[str, Any], *, expected_version: int | None = None) -> dict[str, Any]:
        schemas = CREATE_SCHEMAS if expected_version is None else PATCH_SCHEMAS
        try:
            body = schemas[kind].model_validate(
                dict(fields) if expected_version is None else {**fields, 'expected_version': expected_version}
            )
        except (ValidationError, KeyError) as exc:
            raise _error('invalid_admin_fields', '管理字段无效，请检查必填项与字段格式', 422) from exc
        result = body.model_dump(exclude_unset=expected_version is not None)
        result.pop('expected_version', None)
        return result

    async def _row(self, session: AsyncSession, kind: str, identifier: UUID, *, lock: bool = False) -> AdminRow:
        model = self._model(kind)
        query = select(model).where(model.id == identifier)
        if kind == 'users':
            query = query.where(IdentityUser.deleted_at.is_(None))
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise _error('admin_resource_not_found', '管理记录不存在', 404)
        return cast('AdminRow', row)

    async def _dto(self, session: AsyncSession, kind: str, row: AdminRow) -> dict[str, Any]:
        result = {
            field: getattr(row, field) for field in ('id', 'version', 'created_at', 'updated_at', *DTO_FIELDS[kind])
        }
        if kind == 'users':
            assignments = (
                await session.execute(
                    select(IdentityUserRole.role_id, IdentityRole.name)
                    .join(IdentityRole, IdentityRole.id == IdentityUserRole.role_id)
                    .where(IdentityUserRole.user_id == row.id)
                    .order_by(IdentityUserRole.role_id)
                )
            ).all()
            result['role_ids'] = [role_id for role_id, _ in assignments]
            result['role_names'] = [name for _, name in assignments]
        if kind == 'roles':
            assert isinstance(row, IdentityRole)
            result['permissions'] = without_retired_permissions(row.permissions)
            result['member_count'] = int(
                await session.scalar(
                    select(func.count()).select_from(IdentityUserRole).where(IdentityUserRole.role_id == row.id)
                )
                or 0
            )
        return result

    async def list(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        *,
        search: str = '',
        page: int = 1,
        page_size: int = 20,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        model = self._model(kind)
        principal.require_permission(f'system:{kind}:read')
        self._pagination(page, page_size)
        query = select(model)
        if kind == 'users':
            query = query.where(IdentityUser.deleted_at.is_(None))
        if is_active is not None:
            query = query.where(model.__table__.c.is_active.is_(is_active))
        if search:
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            names = (
                ('username', 'display_name', 'email', 'phone')
                if kind == 'users'
                else (('name', 'code') if kind == 'roles' else ('name',))
            )
            query = query.where(or_(*(getattr(model, name).ilike(f'%{escaped}%', escape='\\') for name in names)))
        total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        order = (model.__table__.c.sort_order, model.id) if kind == 'departments' else (model.id,)
        rows = await session.scalars(query.order_by(*order).offset((page - 1) * page_size).limit(page_size))
        return _page([await self._dto(session, kind, cast('AdminRow', row)) for row in rows], total, page, page_size)

    async def get(self, session: AsyncSession, principal: Principal, kind: str, identifier: UUID) -> dict[str, Any]:
        self._model(kind)
        principal.require_permission(f'system:{kind}:read')
        return await self._dto(session, kind, await self._row(session, kind, identifier))

    @staticmethod
    def _grant_permissions(principal: Principal, permissions: builtins.list[str]) -> builtins.list[str]:
        proposed = frozenset(permissions)
        if not proposed <= PERMISSION_CODES:
            raise _error('unknown_permission', '包含未接通的权限代码')
        if not principal.is_admin and not proposed <= principal.permissions:
            raise _error('permission_escalation', '不能授予超出本人范围的权限', 403)
        return sorted(proposed)

    async def _lock_tree(self, session: AsyncSession, kind: str) -> None:
        model = self._model(kind)
        # A shared ordered lock serializes reparenting, including changes on different nodes.
        await session.execute(select(model.id).order_by(model.id).with_for_update())

    async def _parent(self, session: AsyncSession, kind: str, parent_id: UUID | None, identifier: UUID | None) -> None:
        if parent_id is None:
            return
        visited = {identifier} if identifier is not None else set()
        current: UUID | None = parent_id
        while current is not None:
            if current in visited:
                raise _error('tree_cycle', '上级设置会形成循环')
            visited.add(current)
            parent = await self._row(session, kind, current)
            assert isinstance(parent, IdentityDepartment)
            current = parent.parent_id

    async def _unique(
        self, session: AsyncSession, kind: str, fields: dict[str, Any], identifier: UUID | None = None
    ) -> None:
        key = {'users': 'username', 'roles': 'code'}.get(kind)
        if key and key in fields:
            model = self._model(kind)
            query = select(model.id).where(getattr(model, key) == fields[key])
            if identifier is not None:
                query = query.where(model.id != identifier)
            if await session.scalar(query) is not None:
                raise _error('admin_unique_conflict', '名称代码或路径已存在', 409)

    async def _user_references(
        self,
        session: AsyncSession,
        principal: Principal,
        fields: dict[str, Any],
        *,
        existing_user: IdentityUser | None = None,
    ) -> None:
        if fields.get('department_id') is not None:
            department = await self._row(session, 'departments', fields['department_id'], lock=True)
            assert isinstance(department, IdentityDepartment)
            if not department.is_active and (existing_user is None or existing_user.department_id != department.id):
                raise _error('inactive_department', '不能选择已停用部门')
        for kind, field, membership, reference_key in (
            ('roles', 'role_ids', IdentityUserRole, IdentityUserRole.role_id),
        ):
            if field not in fields:
                continue
            existing_ids = (
                set(await session.scalars(select(reference_key).where(membership.user_id == existing_user.id)))
                if existing_user is not None
                else set()
            )
            fields[field] = sorted(set(fields[field]), key=str)
            for identifier in tuple(fields[field]):
                reference = await self._row(session, kind, identifier, lock=True)
                assert isinstance(reference, IdentityRole)
                if not reference.is_active and identifier not in existing_ids:
                    raise _error('inactive_assignment', '不能分配已停用角色')
                if isinstance(reference, IdentityRole):
                    if reference.system_key is not None:
                        final_admin = fields.get('is_admin', existing_user.is_admin if existing_user else False)
                        if not principal.is_admin or not (final_admin or (existing_user and existing_user.is_admin)):
                            raise _error('builtin_role_assignment_forbidden', '内置角色由超级管理员身份自动维护', 403)
                        fields[field].remove(identifier)
                        continue
                    self._grant_permissions(principal, without_retired_permissions(reference.permissions))

    async def _memberships(self, session: AsyncSession, user_id: UUID, fields: dict[str, Any]) -> None:
        for field, model, key in (('role_ids', IdentityUserRole, 'role_id'),):
            if field in fields:
                await session.execute(delete(model).where(model.user_id == user_id))
                session.add_all([model(user_id=user_id, **{key: identifier}) for identifier in fields.pop(field)])
        await session.flush()

    @staticmethod
    def _protect_builtin_role(row: IdentityRole) -> None:
        if row.system_key is not None:
            raise _error('builtin_role_protected', '系统内置角色由系统维护，不能修改或删除', 409)

    @staticmethod
    def _manage_user(principal: Principal, row: IdentityUser | None, fields: dict[str, Any]) -> None:
        if not principal.is_admin and ((row is not None and row.is_admin) or fields.get('is_admin', False)):
            raise _error('super_admin_protected', '仅超级管理员可管理超级管理员账号', 403)
        if (
            row is not None
            and row.id == principal.id
            and (fields.get('is_active') is False or (row.is_admin and fields.get('is_admin') is False))
        ):
            raise _error('self_account_protected', '不能停用本人或撤销本人的超级管理员身份', 409)

    async def _admin_guard(self, session: AsyncSession) -> builtins.list[UUID]:
        return list(
            await session.scalars(
                select(IdentityUser.id)
                .where(
                    IdentityUser.is_admin.is_(True),
                    IdentityUser.is_active.is_(True),
                    IdentityUser.deleted_at.is_(None),
                )
                .order_by(IdentityUser.id)
                .with_for_update()
            )
        )

    async def _manage_user_roles(self, session: AsyncSession, principal: Principal, user_id: UUID) -> None:
        if principal.is_admin:
            return
        roles = await session.scalars(
            select(IdentityRole).join(IdentityUserRole).where(IdentityUserRole.user_id == user_id)
        )
        for role in roles:
            self._grant_permissions(principal, without_retired_permissions(role.permissions))

    async def _cas(
        self,
        session: AsyncSession,
        kind: str,
        row: AdminRow,
        fields: dict[str, Any],
        expected_version: int,
    ) -> None:
        model = self._model(kind)
        if row.version != expected_version:
            raise _error('version_conflict', '记录已被其他操作修改，请刷新后重试', 409)
        changed = await session.scalar(
            update(model)
            .where(model.id == row.id, model.version == expected_version)
            .values(**fields, version=expected_version + 1, updated_at=datetime.now(timezone.utc))
            .returning(model.id)
            .execution_options(synchronize_session=False)
        )
        if changed is None:
            raise _error('version_conflict', '记录已被其他操作修改，请刷新后重试', 409)
        await session.refresh(row)

    async def create(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        model = self._model(kind)
        principal.require_permission(f'system:{kind}:write')
        values = self._validated(kind, fields)
        await self._unique(session, kind, values)
        if kind == 'departments':
            await self._lock_tree(session, kind)
            await self._parent(session, kind, values.get('parent_id'), None)
        if kind == 'roles':
            values['permissions'] = self._grant_permissions(principal, values['permissions'])
        if kind == 'users':
            self._manage_user(principal, None, values)
            await self._user_references(session, principal, values)
            password: SecretStr = values.pop('password')
            user = await self.identity.create_user(
                session,
                username=values.pop('username'),
                password=password.get_secret_value(),
                is_admin=values.pop('is_admin'),
            )
            row = await self._row(session, 'users', user.id)
            await self._memberships(session, row.id, values)
            for key, value in values.items():
                setattr(row, key, value)
            assert isinstance(row, IdentityUser)
            await sync_builtin_role_membership(session, row.id, is_admin=row.is_admin)
        else:
            row = model(**values)
            session.add(row)
        await session.flush()
        return await self._dto(session, kind, row)

    async def update(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        identifier: UUID,
        fields: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        self._model(kind)
        principal.require_permission(f'system:{kind}:write')
        values = self._validated(kind, fields, expected_version=expected_version)
        admins = await self._admin_guard(session) if kind == 'users' else []
        if kind == 'departments':
            await self._lock_tree(session, kind)
        row = await self._row(session, kind, identifier, lock=True)
        if row.version != expected_version:
            raise _error('version_conflict', '记录已被其他操作修改，请刷新后重试', 409)
        revoke_sessions = False
        if isinstance(row, IdentityUser):
            self._manage_user(principal, row, values)
            await self._manage_user_roles(session, principal, row.id)
            revoke_sessions = values.get('is_active') is False or (
                'is_admin' in values and values['is_admin'] != row.is_admin
            )
            if (
                row.is_admin
                and row.is_active
                and (values.get('is_active') is False or values.get('is_admin') is False)
                and len(admins) <= 1
            ):
                raise _error('last_admin_protected', '必须保留至少一个启用的超级管理员', 409)
            await self._user_references(session, principal, values, existing_user=row)
            await self._memberships(session, row.id, values)
        if isinstance(row, IdentityRole):
            self._protect_builtin_role(row)
            self._grant_permissions(principal, without_retired_permissions(row.permissions))
            if 'permissions' in values:
                historical = set(row.permissions) - set(without_retired_permissions(row.permissions))
                requested = [code for code in values['permissions'] if code not in historical]
                values['permissions'] = self._grant_permissions(principal, requested)
        if kind == 'departments' and 'parent_id' in values:
            await self._parent(session, kind, values['parent_id'], identifier)
        await self._unique(session, kind, values, identifier)
        await self._cas(session, kind, row, values, expected_version)
        if isinstance(row, IdentityUser):
            await sync_builtin_role_membership(session, row.id, is_admin=row.is_admin)
        if revoke_sessions:
            await session.execute(delete(IdentitySession).where(IdentitySession.user_id == row.id))
        return await self._dto(session, kind, row)

    async def delete(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        identifier: UUID,
        *,
        expected_version: int,
    ) -> None:
        model = self._model(kind)
        principal.require_permission(f'system:{kind}:write')
        admins = await self._admin_guard(session) if kind == 'users' else []
        if kind == 'departments':
            await self._lock_tree(session, kind)
        row = await self._row(session, kind, identifier, lock=True)
        if row.version != expected_version:
            raise _error('version_conflict', '记录已被其他操作修改，请刷新后重试', 409)
        if isinstance(row, IdentityUser):
            self._manage_user(principal, row, {})
            await self._manage_user_roles(session, principal, row.id)
            if row.id == principal.id:
                raise _error('self_account_protected', '不能删除本人账号', 409)
            if row.is_admin and row.is_active and len(admins) <= 1:
                raise _error('last_admin_protected', '必须保留至少一个启用的超级管理员', 409)
            await self._cas(
                session, kind, row, {'is_active': False, 'deleted_at': datetime.now(timezone.utc)}, expected_version
            )
            await session.execute(delete(IdentitySession).where(IdentitySession.user_id == row.id))
            return
        if isinstance(row, IdentityRole):
            self._protect_builtin_role(row)
            self._grant_permissions(principal, without_retired_permissions(row.permissions))
        checks = []
        if kind == 'departments':
            checks.append(select(model.id).where(model.__table__.c.parent_id == identifier).limit(1))
        if kind == 'departments':
            checks.append(select(IdentityUser.id).where(IdentityUser.department_id == identifier).limit(1))
        if kind == 'roles':
            checks.append(select(IdentityUserRole.user_id).where(IdentityUserRole.role_id == identifier).limit(1))
        for query in checks:
            if await session.scalar(query) is not None:
                raise _error('resource_in_use', '记录存在下级或关联用户，不能删除', 409)
        removed = await session.scalar(
            delete(model).where(model.id == identifier, model.version == expected_version).returning(model.id)
        )
        if removed is None:
            raise _error('version_conflict', '记录已被其他操作修改，请刷新后重试', 409)

    async def reset_password(
        self,
        session: AsyncSession,
        principal: Principal,
        identifier: UUID,
        password: str,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        principal.require_permission('system:users:write')
        try:
            PasswordReset(password=password, expected_version=expected_version)
        except ValidationError as exc:
            raise _error('invalid_password', '密码长度须为 12 到 1024 个字符', 422) from exc
        row = await self._row(session, 'users', identifier, lock=True)
        assert isinstance(row, IdentityUser)
        self._manage_user(principal, row, {})
        await self._manage_user_roles(session, principal, row.id)
        password_hash = await self.identity.hash_password(password)
        await self._cas(session, 'users', row, {'password_hash': password_hash}, expected_version)
        await session.execute(delete(IdentitySession).where(IdentitySession.user_id == identifier))
        return await self._dto(session, 'users', row)

    async def user_options(self, session: AsyncSession, principal: Principal) -> dict[str, Any]:
        if not principal.is_admin and not principal.permissions.intersection(
            {'system:users:read', 'system:users:write'}
        ):
            raise _error('forbidden', '需要用户管理权限', 403)
        result: dict[str, Any] = {}
        for kind, model in (('departments', IdentityDepartment), ('roles', IdentityRole)):
            rows = await session.scalars(select(model).where(model.is_active.is_(True)).order_by(model.name, model.id))
            result[kind] = [
                {
                    'id': row.id,
                    'name': row.name,
                    **({'system_key': row.system_key} if isinstance(row, IdentityRole) else {}),
                }
                for item in rows
                for row in [cast('IdentityDepartment | IdentityRole', item)]
                if not isinstance(row, IdentityRole)
                or principal.is_admin
                or (
                    row.system_key is None
                    and frozenset(without_retired_permissions(row.permissions)) <= principal.permissions
                )
            ]
        return result

    async def list_sessions(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        current_token: str | None = None,
        search: str = '',
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        principal.require_permission('system:sessions:read')
        self._pagination(page, page_size)
        query = (
            select(IdentitySession, IdentityUser.username)
            .join(IdentityUser)
            .where(
                IdentitySession.expires_at > datetime.now(timezone.utc),
                IdentityUser.is_active.is_(True),
                IdentityUser.deleted_at.is_(None),
            )
        )
        if search:
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            query = query.where(IdentityUser.username.ilike(f'%{escaped}%', escape='\\'))
        total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        rows = await session.execute(
            query.order_by(IdentitySession.created_at.desc().nullslast(), IdentitySession.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        current_hash = self.identity._hash_token(current_token) if current_token else None
        return _page(
            [
                {
                    'session_id': row.id,
                    'user_id': row.user_id,
                    'username': username,
                    'version': row.version,
                    'created_at': row.created_at,
                    'expires_at': row.expires_at,
                    'is_current': row.token_hash == current_hash,
                }
                for row, username in rows
            ],
            total,
            page,
            page_size,
        )

    async def revoke_session(
        self,
        session: AsyncSession,
        principal: Principal,
        identifier: UUID,
        *,
        current_token: str | None = None,
        expected_version: int,
    ) -> None:
        principal.require_permission('system:sessions:write')
        row = await session.scalar(select(IdentitySession).where(IdentitySession.id == identifier).with_for_update())
        if row is None:
            raise _error('session_not_found', '会话不存在', 404)
        if current_token and row.token_hash == self.identity._hash_token(current_token):
            raise _error('current_session_protected', '不能强制退出当前会话，请使用退出登录', 409)
        target = await session.get(IdentityUser, row.user_id)
        if target is not None and target.is_admin and not principal.is_admin:
            raise _error('super_admin_protected', '仅超级管理员可退出超级管理员会话', 403)
        removed = await session.scalar(
            delete(IdentitySession)
            .where(
                IdentitySession.id == identifier,
                IdentitySession.version == expected_version,
            )
            .returning(IdentitySession.id)
        )
        if removed is None:
            raise _error('version_conflict', '会话版本已变化，请刷新后重试', 409)
