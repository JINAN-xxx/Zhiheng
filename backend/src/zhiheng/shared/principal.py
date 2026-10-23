from dataclasses import dataclass
from uuid import UUID

from zhiheng.shared.errors import AppError


@dataclass(frozen=True)
class Principal:
    id: UUID
    username: str
    is_admin: bool

    def require_admin(self) -> None:
        if not self.is_admin:
            raise AppError('forbidden', '需要管理员权限', 403)
