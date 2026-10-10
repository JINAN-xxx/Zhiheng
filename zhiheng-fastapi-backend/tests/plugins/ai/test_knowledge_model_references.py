import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from plugins.ai.knowledge.metadata import MetadataStore
from plugins.ai.knowledge.model_references import has_model_references, model_reference_write
from plugins.ai.knowledge.models import DatabaseCreate, KnowledgeError
from plugins.ai.knowledge.runtime import KnowledgeRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'reference',
    [
        {'embed_model_id': 'target'},
        {'llm_model_id': 'target'},
        {'index_spec': {'model_id': 'target'}},
        {'graph_config': {'llm_model_id': 'target'}},
        {'graph_versions': {'old': {'config': {'llm_model_id': 'target'}}}},
        {'graph_versions': {'old': {'model_snapshot': {'platform_model_id': 'target'}}}},
        {'graph_preview': {'config': {'llm_model_id': 'target'}, 'status': 'completed'}},
    ],
)
async def test_persistent_knowledge_config_versions_and_snapshots_count_as_references(
    tmp_path: Path, reference: dict
) -> None:
    metadata = MetadataStore(tmp_path)
    async with metadata.transaction() as state:
        state['databases']['kb'] = {'db_id': 'kb', **reference}
    assert await has_model_references(metadata, 'target')
    assert not await has_model_references(metadata, 'other')


@pytest.mark.asyncio
async def test_corrupt_metadata_cannot_appear_unreferenced(tmp_path: Path) -> None:
    metadata = MetadataStore(tmp_path)
    metadata.path.write_text('{not-json')
    with pytest.raises(KnowledgeError):
        await has_model_references(metadata, 'target')


@pytest.mark.asyncio
async def test_reference_validation_and_write_wait_for_deletion_guard(tmp_path: Path) -> None:
    class Writer:
        def __init__(self) -> None:
            self.metadata = MetadataStore(tmp_path)
            self.validated = asyncio.Event()

        @model_reference_write
        async def write(self) -> None:
            self.validated.set()
            async with self.metadata.transaction() as state:
                state['databases']['kb'] = {'embed_model_id': 'target'}

    writer = Writer()
    async with MetadataStore(tmp_path).lock('model-references'):
        pending = asyncio.create_task(writer.write())
        await asyncio.sleep(0)
        assert not writer.validated.is_set()
        assert not await has_model_references(writer.metadata, 'target')
    await asyncio.wait_for(pending, 2)
    assert await has_model_references(writer.metadata, 'target')


@pytest.mark.asyncio
async def test_disabled_graph_still_validates_any_llm_id_it_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_llm = 'mdl_' + uuid4().hex
    embedding = 'mdl_' + uuid4().hex
    runtime = KnowledgeRuntime(
        tmp_path, model_service_factory=None, owner_authorizer=None, store_factory=lambda _: object()
    )

    async def resolve(model_id: str, kind: str) -> None:
        if model_id == missing_llm:
            raise KnowledgeError.unavailable()

    monkeypatch.setattr(runtime, '_resolve', AsyncMock(side_effect=resolve))
    with pytest.raises(KnowledgeError):
        await runtime.create_database(
            DatabaseCreate(
                database_name='KB', embed_model_id=embedding, llm_model_id=missing_llm, enable_knowledge_graph=False
            )
        )
    assert (await runtime.metadata.read())['databases'] == {}
