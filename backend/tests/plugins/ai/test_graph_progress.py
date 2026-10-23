from plugins.ai.knowledge import graph_progress


def test_graph_progress_counts_only_current_document_chunks_and_hides_raw_logs() -> None:
    status = {'status': 'processing', 'chunks_list': ['chunk-a', 'chunk-b'], 'chunks_count': 2}
    history = [
        'private token document content',
        'Chunk 1 of 3 extracted 2 Ent + 1 Rel chunk-other',
        'Chunk 2 of 3 extracted 2 Ent + 1 Rel chunk-a',
        'Chunk 3 of 3 extracted 2 Ent + 1 Rel chunk-a',
    ]
    progress = graph_progress.document_progress('doc-a', status, history)
    assert progress == {'stage': 'extracting', 'total_chunks': 2, 'extracted_chunks': 1}


def test_graph_progress_prefers_active_embedding_and_marks_complete_only_from_storage() -> None:
    status = {'status': 'processing', 'chunks_count': 1, 'chunks_list': ['chunk-a']}
    history = ['Phase 1: Processing 2 entities from doc-a (async: 1)']
    result = graph_progress.document_progress('doc-a', status, history, embedding_active=True)
    assert result['stage'] == 'embedding'
    assert result['extracted_chunks'] == 1
    assert graph_progress.document_progress('doc-a', dict(status, status='processed'), history)['stage'] == 'completed'
    assert graph_progress.document_progress('doc-a', dict(status, status='failed'), history)['stage'] == 'failed'


def test_custom_chunk_progress_uses_pending_journal_ids_and_not_committed_base() -> None:
    status = {
        'status': 'processing',
        'chunks_count': 0,
        'metadata': {'custom_chunk_patch': {'chunk_ids': ['chunk-a'], 'phase': 'applying'}},
    }
    assert graph_progress.document_progress('doc-a', status, []) == {
        'stage': 'graph_indexing',
        'total_chunks': 1,
        'extracted_chunks': 1,
    }


def test_pending_document_unknown_chunk_count_does_not_leak_sdk_negative_sentinel() -> None:
    assert graph_progress.document_progress('doc-a', {'status': 'pending', 'chunks_count': -1}, []) == {
        'stage': 'preparing',
        'total_chunks': 0,
        'extracted_chunks': 0,
    }
