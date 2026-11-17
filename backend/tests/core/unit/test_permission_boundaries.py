"""Canonical permissions preserve cross-module OR, directory AND, and account ownership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.core.permission_support import grants
from zhiheng.application.billing.management import BillingManagementService
from zhiheng.modules.billing.queries import CreditQueries
from zhiheng.modules.files.queries import FileCatalog
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@pytest.mark.parametrize(
    'permissions',
    [
        grants('ai:rates'),
        grants('platform:models', 'config_read'),
        grants('platform:models', 'create'),
        grants('platform:models', 'update'),
        grants('platform:models', 'delete'),
        grants('platform:models', 'toggle'),
    ],
)
async def test_private_model_quote_accepts_either_rate_read_or_an_explicit_model_management_action(
    permissions: frozenset[str],
) -> None:
    quoted = object()
    rates = SimpleNamespace(quote=AsyncMock(return_value=quoted))
    catalog = SimpleNamespace(is_public=AsyncMock(return_value=False), billing_metadata=AsyncMock())
    service = BillingManagementService(
        SimpleNamespace(rates=rates), None, None, SimpleNamespace(catalog=catalog), None, None, None
    )
    actor = Principal(uuid4(), 'explicit-operator', False, permissions)
    assert await service.quote(None, actor, uuid4()) is quoted
    catalog.billing_metadata.assert_awaited_once()


@pytest.mark.parametrize(
    'permissions',
    [
        grants('platform:models'),
        grants('platform:models', 'test'),
        grants('ai:credentials'),
        frozenset({'ai:rates:read'}),
        frozenset({'platform:models:read', 'platform:models:update'}),
    ],
)
async def test_private_quote_does_not_expand_to_directory_test_credential_or_disabled_grants(
    permissions: frozenset[str],
) -> None:
    rates = SimpleNamespace(quote=AsyncMock())
    catalog = SimpleNamespace(is_public=AsyncMock(return_value=False), billing_metadata=AsyncMock())
    service = BillingManagementService(
        SimpleNamespace(rates=rates), None, None, SimpleNamespace(catalog=catalog), None, None, None
    )
    with pytest.raises(AppError) as error:
        await service.quote(None, Principal(uuid4(), 'other-reader', False, permissions), uuid4())
    assert error.value.status_code == 404
    catalog.billing_metadata.assert_not_awaited()
    rates.quote.assert_not_awaited()


@pytest.mark.parametrize(
    'permissions,allowed',
    [
        (grants('ai:storage'), False),
        (grants('system:users'), False),
        (grants('ai:storage') | {'system:users:read'}, False),
        (grants('ai:storage') | grants('system:users'), True),
    ],
)
async def test_file_user_directory_requires_both_enabled_modules(
    permissions: frozenset[str],
    allowed: bool,
) -> None:
    identity = SimpleNamespace(directory=AsyncMock(return_value=[]))
    queries = FileCatalog(None, identity)
    actor = Principal(uuid4(), 'directory-operator', False, permissions)
    if allowed:
        assert await queries.users(None, actor) == []
        identity.directory.assert_awaited_once()
    else:
        with pytest.raises(AppError) as error:
            await queries.users(None, actor)
        assert error.value.status_code == 403
        identity.directory.assert_not_awaited()


def test_account_reads_remain_owner_scoped_without_enabled_credit_read() -> None:
    owner = Principal(uuid4(), 'owner', False)
    CreditQueries._read_account(owner, owner.id)
    for permissions in (frozenset(), frozenset({'ai:credits:read'}), grants('platform:usage')):
        actor = Principal(uuid4(), 'other', False, permissions)
        with pytest.raises(AppError) as error:
            CreditQueries._read_account(actor, owner.id)
        assert error.value.status_code == 403
    operator = Principal(uuid4(), 'credit-reader', False, grants('ai:credits'))
    CreditQueries._read_account(operator, owner.id)
