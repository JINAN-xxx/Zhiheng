"""The canonical module/operation directory shared by roles and request authorization."""

from zhiheng.shared.permission_policy import PERMISSION_CODES, catalog

__all__ = ['PERMISSION_CODES', 'permission_catalog', 'without_retired_permissions']


def without_retired_permissions(codes: list[str]) -> list[str]:
    return [code for code in codes if code in PERMISSION_CODES]


def permission_catalog() -> list[dict[str, str]]:
    return catalog()
