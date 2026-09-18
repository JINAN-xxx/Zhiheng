"""把 LightRAG 的真实文档状态投影为有限进度，原始日志不离开后端。"""

import re

_EXTRACTED = re.compile(r'Chunk \d+ of \d+ extracted \d+ Ent \+ \d+ Rel (\S+)')


def document_progress(doc_id: str, status: dict | None, history: list[str], *, embedding_active: bool = False) -> dict:
    status = status or {}
    journal = (status.get('metadata') or {}).get('custom_chunk_patch') or {}
    chunk_ids = set(journal.get('chunk_ids') or status.get('chunks_list') or [])
    total = len(chunk_ids) or max(0, int(status.get('chunks_count') or 0))
    extracted = set()
    merging = journal.get('phase') == 'applying'
    for message in history:
        match = _EXTRACTED.search(message)
        if match and match[1] in chunk_ids:
            extracted.add(match[1])
        if (re.search(r'Phase [012]: ', message) and f' {doc_id}:' in message) or (
            re.search(r'Phase [12]: ', message) and f' from {doc_id} ' in message
        ):
            merging = True
    count = total if merging else len(extracted)
    persisted_status = str(status.get('status', '')).lower()
    if persisted_status in {'processed', 'docstatus.processed'}:
        stage, count = 'completed', total
    elif persisted_status in {'failed', 'docstatus.failed'}:
        stage = 'failed'
    elif embedding_active:
        stage = 'embedding'
    elif merging:
        stage = 'graph_indexing'
    else:
        stage = 'extracting' if total else 'preparing'
    return {'stage': stage, 'total_chunks': total, 'extracted_chunks': min(total, count)}
