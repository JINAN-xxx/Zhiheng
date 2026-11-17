"""输入预算的保守估计与会话来源指纹。

Estimates are an offline safety heuristic, not provider tokenizer measurements.
Ledger reservation deliberately keeps its existing, separate conservative measure.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from zhiheng.modules.models.public import ModelMessage


@dataclass(frozen=True)
class ContextTurn:
    question_id: UUID
    reply_id: UUID
    question: ModelMessage
    reply: ModelMessage
    generation_index: int = 1
    file_refs: tuple[tuple[UUID, str], ...] = ()
    source_digest: str | None = None


def source_refs(turns: Sequence[ContextTurn]) -> list[dict[str, object]]:
    return [
        {
            'question_id': str(turn.question_id),
            'reply_id': str(turn.reply_id),
            'generation_index': turn.generation_index,
            'content_hash': turn.source_digest
            or hashlib.sha256(
                json.dumps(
                    [
                        turn.question.content,
                        turn.reply.content,
                        [hashlib.sha256(image.data_url.encode()).hexdigest() for image in turn.question.images],
                        [(str(file_id), sha) for file_id, sha in turn.file_refs],
                    ],
                    ensure_ascii=False,
                    separators=(',', ':'),
                ).encode()
            ).hexdigest(),
        }
        for turn in turns
    ]


def source_fingerprint(turns: Sequence[ContextTurn]) -> str:
    return hashlib.sha256(json.dumps(source_refs(turns), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def attachment_refs(turns: Sequence[ContextTurn]) -> list[dict[str, object]]:
    return [
        {'file_id': str(file_id), 'sha256': sha, 'question_id': str(turn.question_id)}
        for turn in turns
        for file_id, sha in turn.file_refs
    ]
