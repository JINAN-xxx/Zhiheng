from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from exceptions.exception import ServiceException
from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTask, AgentWorkTaskMember
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage, AiChatRun, AiChatSession
from plugins.ai.entity.do.ai_developer_api_do import (
    AiApiKey,
    AiApiKeyModelGrant,
    AiApiUsageLedger,
    AiApplication,
    AiModelRateCard,
)
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.infrastructure.model_reference_provider import ModelDeletionReferences
from plugins.ai.knowledge.metadata import MetadataStore
from plugins.ai.service.model_catalog_service import ModelCatalogService
from plugins.core.discovery.registry import PluginRegistry
from plugins.core.discovery.scanner import DiscoveredPlugin
from plugins.core.manifest.schema import PluginManifest

MODEL = 'mdl_00000000000040008000000000000001'
BINDING = 'bnd_00000000000040008000000000000001'


REFERENCE_TABLES = [
    AiModelCatalog,
    AiResourceBinding,
    AiApplication,
    AiApiKey,
    AiApiKeyModelGrant,
    AiModelRateCard,
    AiApiUsageLedger,
    AiChatSession,
    AiChatMessage,
    AiChatRun,
    AgentConfiguration,
    AgentWorkTask,
    AgentWorkTaskMember,
    AgentWorkMessage,
]


def agent_registry(*, enabled: bool = True) -> PluginRegistry:
    root = Path(__file__).resolve().parents[3] / 'plugins/agent_extension'
    manifest = PluginManifest.model_validate(yaml.safe_load((root / 'plugin.yaml').read_text()))
    discovered = DiscoveredPlugin(manifest=manifest, backend_path=root, manifest_path=root / 'plugin.yaml')
    record = SimpleNamespace(
        plugin_id=manifest.id, installed_version=manifest.version, enabled='0' if enabled else '1', status='installed'
    )
    return PluginRegistry.build([discovered], [record])


@pytest_asyncio.fixture
async def deletion_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        for model in REFERENCE_TABLES:
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(
            AiModelCatalog(
                model_id=MODEL, model_code='model', display_name='Model', model_type='chat', lifecycle_status='disabled'
            )
        )
        await db.flush()
        db.add(
            AiResourceBinding(
                binding_id=BINDING,
                resource_type='model',
                resource_id=MODEL,
                engine_type='litellm',
                engine_resource_id='runtime-model',
                binding_status='active',
            )
        )
        await db.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'kind', ['unreferenced', 'chat', 'agent-disabled-plugin', 'knowledge', 'corrupt-metadata', 'missing-registry']
)
async def test_real_catalog_delete_preserves_all_data_when_reference_check_blocks(
    deletion_db: async_sessionmaker[AsyncSession], tmp_path: Path, kind: str
) -> None:
    metadata = MetadataStore(tmp_path)
    if kind == 'knowledge':
        async with metadata.transaction() as state:
            state['databases']['kb'] = {'graph_versions': {'old': {'config': {'llm_model_id': MODEL}}}}
    if kind == 'corrupt-metadata':
        metadata.path.write_text('{broken')
    async with deletion_db() as db:
        if kind == 'chat':
            db.add(AiChatSession(session_id='session', user_id=1, model_id=MODEL, status='archived'))
        if kind == 'agent-disabled-plugin':
            db.add(AgentConfiguration(agent_id='agent', owner_user_id=1, name='Agent', model_id=MODEL))
        await db.commit()
        registry = None if kind == 'missing-registry' else agent_registry(enabled=False)
        checker = ModelDeletionReferences(db, registry=registry, metadata=metadata)
        service = ModelCatalogService(db, None, checker)
        if kind == 'unreferenced':
            await service.delete_model(MODEL)
        else:
            with pytest.raises(ServiceException):
                await service.delete_model(MODEL)
        assert not db.in_transaction()
    async with deletion_db() as db:
        assert (await db.get(AiModelCatalog, MODEL) is None) is (kind == 'unreferenced')
        assert (await db.get(AiResourceBinding, BINDING) is None) is (kind == 'unreferenced')


@pytest.mark.asyncio
async def test_catalog_without_reference_checker_cannot_silently_delete(
    deletion_db: async_sessionmaker[AsyncSession],
) -> None:
    async with deletion_db() as db:
        with pytest.raises(ServiceException) as caught:
            await ModelCatalogService(db, None).delete_model(MODEL)
        assert '引用检查不可用' in caught.value.message
        assert not db.in_transaction()
        assert await db.get(AiModelCatalog, MODEL) is not None
