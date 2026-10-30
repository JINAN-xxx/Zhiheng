"""Current dependency descriptions with ownership-aware management disclosure."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@dataclass(frozen=True)
class ResourceReference:
    kind: str
    id: UUID
    name: str
    status: str
    owner_id: UUID | None = None
    path: str | None = None


def reference_conflict(
    code: str, message: str, principal: Principal, references: Sequence[ResourceReference]
) -> AppError:
    labels = {
        'agent': '智能体',
        'work': '工作任务',
        'knowledge': '知识库',
        'key': '密钥',
        'run': '运行',
        'chat': '对话',
    }
    details: list[dict[str, object]] = []
    for item in references:
        visible = principal.is_admin or item.owner_id is None or item.owner_id == principal.id
        details.append(
            {
                'kind': item.kind,
                'id': str(item.id) if visible else None,
                'name': item.name if visible else f'其他用户的{labels.get(item.kind, "资源")}（无权查看）',
                'status': item.status if visible else 'referenced',
                'path': item.path if visible else None,
            }
        )
    return AppError(code, message, 409, details={'references': details})
