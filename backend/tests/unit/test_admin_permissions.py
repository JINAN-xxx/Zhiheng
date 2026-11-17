import ast
import re
from pathlib import Path
from uuid import uuid4

import pytest

from zhiheng.shared.errors import AppError
from zhiheng.shared.permission_policy import PERMISSION_CODES, effective_permissions, is_granted
from zhiheng.shared.principal import Principal


def test_existing_super_admin_has_console_permissions() -> None:
    Principal(uuid4(), 'admin', True).require_permission('system:users:create')


def test_role_permission_only_allows_its_exact_action() -> None:
    member = Principal(uuid4(), 'reader', False, frozenset({'system:users:enabled', 'system:users:read'}))
    member.require_permission('system:users:read')
    with pytest.raises(AppError) as error:
        member.require_permission('system:users:create')
    assert error.value.status_code == 403
    with pytest.raises(AppError):
        member.require_admin()


def test_regular_account_cannot_enter_console() -> None:
    with pytest.raises(AppError) as error:
        Principal(uuid4(), 'member', False).require_permission('system:monitor:read')
    assert error.value.status_code == 403


def test_any_permission_preserves_or_between_explicit_actions() -> None:
    manager = Principal(
        uuid4(),
        'manager',
        False,
        frozenset({'platform:models:enabled', 'platform:models:read', 'platform:models:update'}),
    )
    assert manager.has_any_permission('platform:models:config_read', 'platform:models:update')
    manager.require_any_permission('platform:models:config_read', 'platform:models:update')
    assert not manager.has_any_permission('ai:rates:create')
    with pytest.raises(AppError):
        manager.require_any_permission('ai:rates:read', 'ai:rates:create')
    with pytest.raises(AppError):
        manager.require_permission('platform:models:config_read')


@pytest.mark.parametrize(
    'requested',
    [
        'ai:models:read',
        'ai:models:write',
        'ai:models:test',
        'system:users:write',
        'platform:knowledge:access',
        'platform:knowledge:manage',
        'platform:knowledge:unknown',
    ],
)
def test_old_and_unknown_requests_never_authorize_ordinary_users(requested: str) -> None:
    principal = Principal(uuid4(), 'all-actions', False, PERMISSION_CODES | {requested})
    assert not is_granted(principal.permissions, requested)
    assert not principal.has_any_permission(requested)
    assert requested not in effective_permissions(principal.permissions)
    with pytest.raises(AppError) as error:
        principal.require_permission(requested)
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    'codes,allowed',
    [
        ({'platform:knowledge:create'}, False),
        ({'platform:knowledge:read', 'platform:knowledge:create'}, False),
        ({'platform:knowledge:enabled', 'platform:knowledge:create'}, False),
        ({'platform:knowledge:enabled', 'platform:knowledge:read'}, False),
        ({'platform:knowledge:enabled', 'platform:knowledge:read', 'platform:knowledge:create'}, True),
    ],
)
def test_action_requires_its_gate_read_and_exact_grant(codes: set[str], allowed: bool) -> None:
    assert is_granted(codes, 'platform:knowledge:create') is allowed


def test_empty_permissions_never_authorize_an_ordinary_user() -> None:
    member = Principal(uuid4(), 'member', False)
    assert not member.has_any_permission()
    with pytest.raises(AppError):
        member.require_any_permission()


def test_super_admin_keeps_existing_unrestricted_authority() -> None:
    admin = Principal(uuid4(), 'admin', True)
    assert admin.has_any_permission('platform:models:config_read')
    admin.require_any_permission('platform:models:create', 'ai:rates:create')
    admin.require_permission('unknown:permission')


def test_production_authorization_uses_canonical_codes_including_formatted_requests() -> None:
    source = Path(__file__).resolve().parents[2] / 'src' / 'zhiheng'
    permission_calls = {
        'require_permission',
        'require_any_permission',
        'has_any_permission',
        'is_granted',
        'require_capability',
        'has_capability',
    }
    retired = re.compile(r'(?:^|:)(?:access|manage|write)$')
    code = re.compile(r'(?:platform|ai|system):[a-z_]+:[a-z_]+$')
    violations = []
    for path in source.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and code.fullmatch(node.value)
                and node.value not in PERMISSION_CODES
            ):
                violations.append(f'{path}:{node.lineno}: {node.value}')
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
            if name not in permission_calls:
                continue
            for argument in node.args:
                violations.extend(
                    f'{path}:{value.lineno}: {ast.unparse(argument)}'
                    for value in ast.walk(argument)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str) and retired.search(value.value)
                )
    assert not violations, '\n'.join(violations)
