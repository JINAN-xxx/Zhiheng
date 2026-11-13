from dataclasses import dataclass
from uuid import UUID

from zhiheng.shared.errors import AppError
from zhiheng.shared.permission_policy import PERMISSION_CODES, is_granted


@dataclass(frozen=True)
class Principal:
    id: UUID
    username: str
    is_admin: bool
    permissions: frozenset[str] = frozenset()

    def personal(self) -> 'Principal':
        """Keep functional rights while enforcing ordinary ownership and explicit ACLs."""
        permissions = PERMISSION_CODES if self.is_admin else self.permissions
        return Principal(self.id, self.username, False, permissions)

    def has_any_permission(self, *permissions: str) -> bool:
        return self.is_admin or any(is_granted(self.permissions, permission) for permission in permissions)

    def require_any_permission(self, *permissions: str) -> None:
        if not self.has_any_permission(*permissions):
            raise AppError('forbidden', '当前账号没有此操作权限', 403)

    def require_admin(self) -> None:
        if not self.is_admin:
            raise AppError('forbidden', '需要管理员权限', 403)

    def require_permission(self, permission: str) -> None:
        if not self.has_any_permission(permission):
            raise AppError('forbidden', '当前账号没有此操作权限', 403)
