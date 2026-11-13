"""Candidate quotas and strict rank-to-source mapping shared by retrieval branches."""

import math
from typing import Any

from zhiheng.modules.knowledge.contracts import Hit
from zhiheng.shared.errors import AppError

PER_BASE_CANDIDATES = 40
TOTAL_CANDIDATES = 80
FINAL_HITS = 8
MAX_FINAL_HITS = 15


def balance_candidates(groups: list[list[Hit]], limit: int = TOTAL_CANDIDATES) -> list[Hit]:
    """Round-robin each library's own order; scores across embeddings are incomparable."""
    result: list[Hit] = []
    seen = set()
    for offset in range(PER_BASE_CANDIDATES):
        for group in groups:
            if offset >= len(group):
                continue
            hit = group[offset]
            identity = (hit.knowledge_base_id, hit.chunk_id)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(hit)
            if len(result) >= min(TOTAL_CANDIDATES, limit):
                return result
    return result


def ranking_rows(result: object, count: int) -> list[tuple[int, float]]:
    if not isinstance(result, dict) or not isinstance(result.get('results'), list):
        raise AppError('invalid_knowledge_rerank', '重排结果无效', 502)
    rows = result['results']
    seen: set[int] = set()
    mapped = []
    for item in rows:
        if not isinstance(item, dict):
            raise AppError('invalid_knowledge_rerank', '重排结果无效', 502)
        index, score = item.get('index'), item.get('relevance_score')
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < count
            or index in seen
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
        ):
            raise AppError('invalid_knowledge_rerank', '重排结果索引或分数无效', 502)
        seen.add(index)
        mapped.append((index, float(score)))
    return mapped


def rank_candidates(candidates: list[Hit], result: Any) -> list[Hit]:
    return [
        candidates[index].model_copy(update={'rerank_score': score})
        for index, score in ranking_rows(result, len(candidates))
    ]
