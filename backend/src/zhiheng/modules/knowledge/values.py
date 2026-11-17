"""Immutable build selection and deterministic knowledge fingerprints."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from zhiheng.modules.models.public import ModelBinding
from zhiheng.shared.errors import AppError


@dataclass(frozen=True)
class BuildSelection:
    file_ids: list[UUID] | None = None
    document_ids: list[UUID] | None = None
    folder_id: UUID | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    confirm_references: bool = False

    def __post_init__(self) -> None:
        if self.file_ids is not None and len(set(self.file_ids)) != len(self.file_ids):
            raise AppError('invalid_source_selection', '入库源文件不能重复', 422)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, default=str, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()


def binding_fingerprint(binding: ModelBinding) -> str:
    return fingerprint(binding.model_dump(mode='json', exclude={'max_output_tokens', 'vision_enabled'}))
