import runpy
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

CHECKER_PATH = Path(__file__).resolve().parents[3] / 'tools/check_core_wheel.py'
REQUIRED_MEMBERS = (
    'zhiheng/cli/main.py',
    'zhiheng/__init__.py',
    'zhiheng/py.typed',
    'zhiheng/shared/references.py',
    'zhiheng/application/developer_management.py',
    'zhiheng/modules/billing/public.py',
    'zhiheng/modules/billing/contracts.py',
    'zhiheng/modules/billing/orm.py',
    'zhiheng/modules/billing/service.py',
    'zhiheng/modules/billing/pricing.py',
    'zhiheng/modules/billing/usage.py',
    'zhiheng/modules/billing/ports.py',
    'zhiheng/modules/billing/gate.py',
    'zhiheng/application/billing_management.py',
    'zhiheng/api/routes/billing.py',
    'zhiheng/application/identity/__init__.py',
    'zhiheng/application/identity/accounts.py',
    'zhiheng/modules/skills/public.py',
    'zhiheng/modules/skills/package.py',
    'zhiheng/modules/skills/validation.py',
    'zhiheng/api/routes/skills.py',
    'zhiheng/modules/agents/public.py',
    'zhiheng/modules/agents/LICENSE',
    'zhiheng/modules/agents/context.py',
    'zhiheng/application/agent_runs.py',
    'zhiheng/api/routes/agents.py',
    'zhiheng/api/routes/public_agents.py',
    'zhiheng/modules/work/public.py',
    'zhiheng/application/work_runs.py',
    'zhiheng/application/lifecycle.py',
    'zhiheng/api/routes/work.py',
    'zhiheng/api/routes/files.py',
    'zhiheng/modules/files/public.py',
    'zhiheng/modules/files/jobs.py',
    'zhiheng/infrastructure/file_storage.py',
    'zhiheng/modules/knowledge/adapters/native_worker.py',
    'zhiheng/modules/knowledge/adapters/milvus.py',
    'zhiheng/modules/knowledge/adapters/milvus_rpc.py',
    'zhiheng/modules/knowledge/build.py',
    'zhiheng/modules/knowledge/retrieval.py',
    'zhiheng/modules/knowledge/generations.py',
    'zhiheng/application/knowledge_preparation.py',
    'zhiheng/application/knowledge_jobs.py',
    'zhiheng/api/routes/knowledge.py',
    'zhiheng/api/routes/knowledge_graph.py',
    'zhiheng/application/graph_jobs.py',
    'zhiheng/application/graph_runs.py',
    'zhiheng/modules/knowledge/adapters/lightrag/LICENSE',
    'zhiheng/modules/knowledge/adapters/lightrag/tokenizer.py',
    'zhiheng/modules/knowledge/adapters/lightrag/TIKTOKEN_LICENSE',
    'zhiheng/modules/knowledge/adapters/lightrag/tokenizer_spec.json',
    'zhiheng/modules/knowledge/adapters/lightrag/o200k_base.tiktoken',
    'zhiheng/modules/knowledge/adapters/lightrag/__init__.py',
    'zhiheng/modules/knowledge/adapters/lightrag/policy.py',
    'zhiheng/modules/knowledge/adapters/lightrag/catalog.json',
    'zhiheng/modules/knowledge/adapters/lightrag/factory.py',
    'zhiheng/modules/knowledge/adapters/lightrag/gc.py',
    'zhiheng/modules/knowledge/adapters/lightrag/managed.py',
    'zhiheng/modules/knowledge/adapters/lightrag/owned_loop.py',
    'zhiheng/modules/knowledge/adapters/lightrag/postgres.py',
    'zhiheng/modules/knowledge/adapters/lightrag/registry.py',
    'zhiheng/modules/knowledge/adapters/lightrag/results.py',
    'zhiheng/modules/knowledge/adapters/lightrag/schema.py',
    'zhiheng/modules/knowledge/adapters/lightrag/sdk.py',
    'zhiheng/modules/knowledge/adapters/lightrag/sdk_hashes.json',
    'zhiheng/modules/knowledge/adapters/lightrag/storage.py',
    'zhiheng/modules/knowledge/adapters/lightrag/transport.py',
    'zhiheng/modules/knowledge/adapters/lightrag/vector.py',
    'zhiheng/modules/knowledge/graph/contracts.py',
    'zhiheng/modules/knowledge/graph/lifecycle.py',
    'zhiheng/modules/knowledge/graph/orm.py',
    'zhiheng/modules/knowledge/graph/service.py',
    'zhiheng/modules/knowledge/graph/store.py',
    'zhiheng/worker/graph_runner.py',
    *(
        f'zhiheng_platform-0.1.0.data/data/share/zhiheng/migrations/{name}'
        for name in (
            'env.py',
            'versions/0001_core.py',
            'versions/0002_developer.py',
            'versions/0003_skills.py',
            'versions/0004_agents.py',
            'versions/0005_work.py',
            'versions/0006_files.py',
            'versions/0007_knowledge.py',
            'versions/0008_graph.py',
            'versions/0009_message_actions.py',
            'versions/0010_admin_console.py',
            'versions/0011_builtin_admin_role.py',
            'versions/0012_platform_credits.py',
            'versions/0013_skill_revision_history.py',
            'versions/0014_personal_skill_ownership.py',
            'versions/0015_resource_api_access.py',
        )
    ),
)


@pytest.fixture
def verify_wheel() -> Callable[[Path], Path]:
    namespace = runpy.run_path(str(CHECKER_PATH))
    return cast('Callable[[Path], Path]', namespace['verify_core_wheel'])


def write_wheel(directory: Path, members: tuple[str, ...]) -> Path:
    path = directory / 'zhiheng_platform-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for member in members:
            archive.writestr(member, '')
    return path


def test_accepts_core_and_builtin_cli(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    wheel = write_wheel(tmp_path, REQUIRED_MEMBERS)

    assert verify_wheel(tmp_path) == wheel


@pytest.mark.parametrize(
    'forbidden',
    [
        '.env',
        'zhiheng/.env.production',
        'config/env.py',
        'plugins/ai/hooks.py',
        'legacy/backend/app.py',
        'cli/main.py',
    ],
)
def test_rejects_environment_or_legacy_files(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, forbidden: str
) -> None:
    write_wheel(tmp_path, (*REQUIRED_MEMBERS, forbidden))

    with pytest.raises(ValueError, match='legacy runtime or environment'):
        verify_wheel(tmp_path)


def test_rejects_a_wheel_missing_the_cli(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    write_wheel(tmp_path, ('zhiheng/__init__.py', 'zhiheng/py.typed'))

    with pytest.raises(ValueError, match=r'zhiheng/cli/main\.py'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize(
    'revision',
    [
        '0001_core.py',
        '0002_developer.py',
        '0003_skills.py',
        '0004_agents.py',
        '0005_work.py',
        '0006_files.py',
        '0007_knowledge.py',
        '0008_graph.py',
        '0009_message_actions.py',
        '0010_admin_console.py',
        '0011_builtin_admin_role.py',
        '0012_platform_credits.py',
        '0013_skill_revision_history.py',
        '0014_personal_skill_ownership.py',
        '0015_resource_api_access.py',
    ],
)
def test_requires_all_database_migration_revisions(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, revision: str
) -> None:
    write_wheel(
        tmp_path,
        tuple(name for name in REQUIRED_MEMBERS if not name.endswith(revision)),
    )
    with pytest.raises(ValueError, match='migration'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize('member', ['zhiheng/modules/billing/public.py', 'zhiheng/api/routes/billing.py'])
def test_requires_platform_billing_resources(verify_wheel: Callable[[Path], Path], tmp_path: Path, member: str) -> None:
    write_wheel(tmp_path, tuple(name for name in REQUIRED_MEMBERS if name != member))
    with pytest.raises(ValueError, match='missing'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize('member', ['zhiheng/shared/references.py', 'zhiheng/application/developer_management.py'])
def test_requires_resource_lifecycle_services(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, member: str
) -> None:
    write_wheel(tmp_path, tuple(name for name in REQUIRED_MEMBERS if name != member))
    with pytest.raises(ValueError, match='missing'):
        verify_wheel(tmp_path)
