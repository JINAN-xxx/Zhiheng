"""Verify the new core and compatibility CLI are present in a built wheel."""

import argparse
import zipfile
from pathlib import Path

REQUIRED_FILES = frozenset(
    {
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
    }
)


def verify_core_wheel(directory: Path) -> Path:
    wheels = sorted(directory.glob('zhiheng_platform-*.whl'))
    if len(wheels) != 1:
        raise ValueError(f'Expected one platform wheel in {directory}, found {len(wheels)}')
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    missing = REQUIRED_FILES - names
    if missing:
        raise ValueError(f'Core wheel is missing: {", ".join(sorted(missing))}')
    migrations = (
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
    )
    if any(
        not any(name.endswith('/share/zhiheng/migrations/' + migration) for name in names) for migration in migrations
    ):
        raise ValueError('Core wheel is missing required database migration resources')
    if any(
        name.startswith(('plugins/', 'config/', 'legacy/', 'cli/'))
        or any(part.startswith('.env') for part in Path(name).parts)
        for name in names
    ):
        raise ValueError('Core wheel includes legacy runtime or environment files')
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(f'Core wheel verified: {verify_core_wheel(args.directory)}')


if __name__ == '__main__':
    main()
