"""Verify the new core and compatibility CLI are present in a built wheel."""

import argparse
import zipfile
from pathlib import Path

REQUIRED_FILES = frozenset(
    {
        'zhiheng/cli/main.py',
        'zhiheng/__init__.py',
        'zhiheng/py.typed',
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
