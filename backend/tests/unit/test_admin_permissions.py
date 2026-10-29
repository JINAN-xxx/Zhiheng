from uuid import uuid4

import pytest

from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


def test_existing_super_admin_has_console_permissions() -> None:
    Principal(uuid4(), 'admin', True).require_permission('system:users:write')


def test_role_permission_only_allows_its_exact_action() -> None:
    member = Principal(uuid4(), 'reader', False, frozenset({'system:users:read'}))
    member.require_permission('system:users:read')
    with pytest.raises(AppError) as error:
        member.require_permission('system:users:write')
    assert error.value.status_code == 403
    with pytest.raises(AppError):
        member.require_admin()


def test_regular_account_cannot_enter_console() -> None:
    with pytest.raises(AppError) as error:
        Principal(uuid4(), 'member', False).require_permission('system:monitor:read')
    assert error.value.status_code == 403


def test_any_permission_accepts_an_explicit_management_capability_only() -> None:
    manager = Principal(uuid4(), 'manager', False, frozenset({'ai:models:write'}))
    assert manager.has_any_permission('ai:models:read', 'ai:models:write')
    manager.require_any_permission('ai:models:read', 'ai:models:write')
    assert not manager.has_any_permission('ai:rates:write')
    with pytest.raises(AppError):
        manager.require_any_permission('ai:rates:read', 'ai:rates:write')
    with pytest.raises(AppError):
        manager.require_permission('ai:models:read')


def test_empty_permissions_never_authorize_an_ordinary_user() -> None:
    member = Principal(uuid4(), 'member', False)
    assert not member.has_any_permission()
    with pytest.raises(AppError):
        member.require_any_permission()


def test_super_admin_keeps_access_to_ai_capabilities() -> None:
    admin = Principal(uuid4(), 'admin', True)
    assert admin.has_any_permission('ai:models:read')
    admin.require_any_permission('ai:models:write', 'ai:rates:write')
