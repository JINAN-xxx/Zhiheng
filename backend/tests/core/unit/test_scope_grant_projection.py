from uuid import uuid4

import pytest

from zhiheng.modules.developer.scopes import require_key_capabilities, scope_catalog
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


def user(*permissions: str) -> Principal:
    return Principal(
        uuid4(),
        'member',
        False,
        frozenset(
            {
                'platform:developer:enabled',
                'platform:developer:read',
                'platform:developer:key_create',
                *permissions,
            }
        ),
    )


@pytest.mark.parametrize(
    ('permissions', 'allowed', 'denied'),
    [
        (
            ('platform:knowledge:enabled', 'platform:knowledge:read'),
            ('knowledge:read',),
            ('knowledge:query', 'knowledge:write'),
        ),
        (
            ('platform:knowledge:enabled', 'platform:knowledge:read', 'platform:knowledge:query'),
            ('knowledge:read', 'knowledge:query'),
            ('knowledge:write',),
        ),
        (('platform:skills:enabled', 'platform:skills:read'), ('skills:read',), ('skills:write',)),
        (
            ('platform:skills:enabled', 'platform:skills:read', 'platform:skills:delete'),
            ('skills:read', 'skills:write'),
            (),
        ),
        (('platform:agents:enabled', 'platform:agents:read'), (), ('agents:invoke',)),
        (('platform:agents:enabled', 'platform:agents:read', 'platform:agents:use'), ('agents:invoke',), ()),
    ],
)
def test_catalog_matches_issuance_without_guessing_read_as_use(
    permissions: tuple[str, ...], allowed: tuple[str, ...], denied: tuple[str, ...]
) -> None:
    principal = user(*permissions)
    definitions = {item['scope']: item for item in scope_catalog(principal)['definitions']}
    for scope in allowed:
        assert definitions[scope]['grantable'] is True and definitions[scope]['reason'] is None
        require_key_capabilities(principal, (scope,), operation='key_create')
    for scope in denied:
        assert definitions[scope]['grantable'] is False and definitions[scope]['reason']
        with pytest.raises(AppError) as error:
            require_key_capabilities(principal, (scope,), operation='key_create')
        assert error.value.status_code == 403


def test_catalog_revocation_reflects_key_create_and_module_gate_independently() -> None:
    principal = user('platform:knowledge:enabled', 'platform:knowledge:read')
    assert next(x for x in scope_catalog(principal)['definitions'] if x['scope'] == 'knowledge:read')['grantable']
    for removed in ['platform:developer:key_create', 'platform:developer:enabled', 'platform:knowledge:enabled']:
        current = Principal(principal.id, principal.username, False, principal.permissions - {removed})
        assert not next(x for x in scope_catalog(current)['definitions'] if x['scope'] == 'knowledge:read')['grantable']
