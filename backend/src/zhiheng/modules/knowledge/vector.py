"""Vector index identity and runtime capabilities, independent of the SDK."""

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from zhiheng.modules.knowledge.control import VectorIOControl
from zhiheng.shared.errors import AppError

HYBRID_RETRIEVAL_VERSION = 2


@dataclass(frozen=True)
class VectorSpec:
    base_id: UUID
    revision: UUID
    attempt_id: UUID
    collection_name: str
    fingerprint: str
    dimension: int | None
    retrieval_version: int = 1

    @property
    def hybrid(self) -> bool:
        return self.retrieval_version == HYBRID_RETRIEVAL_VERSION

    def description(self) -> str:
        expected = f'zh_k_{self.base_id.hex}_{self.revision.hex}_{self.attempt_id.hex}'
        if (
            self.collection_name != expected
            or not re.fullmatch('[a-f0-9]{64}', self.fingerprint)
            or self.dimension is None
            or self.retrieval_version not in {1, 2}
        ):
            raise AppError('invalid_index_identity', '索引身份无效', 503)
        return json.dumps(
            {
                'base': str(self.base_id),
                'revision': str(self.revision),
                'attempt': str(self.attempt_id),
                'fingerprint': self.fingerprint,
                'dimension': self.dimension,
                **({'retrieval_version': self.retrieval_version} if self.retrieval_version != 1 else {}),
            },
            sort_keys=True,
        )


class VectorPort(Protocol):
    async def available(self) -> bool: ...

    async def build(
        self, spec: VectorSpec, rows: list[dict[str, object]], *, control: VectorIOControl | None = None
    ) -> None: ...

    async def search(
        self,
        spec: VectorSpec,
        vector: list[float],
        limit: int,
        document_ids: list[UUID],
        *,
        timeout_seconds: float = 8,
    ) -> list[dict[str, Any]]: ...

    async def drop(self, spec: VectorSpec) -> None: ...

    async def hybrid_search(
        self,
        spec: VectorSpec,
        vector: list[float],
        query: str,
        limit: int,
        document_ids: list[UUID],
        *,
        timeout_seconds: float = 8,
    ) -> list[dict[str, Any]]: ...
