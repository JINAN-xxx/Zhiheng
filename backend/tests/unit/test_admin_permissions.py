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
