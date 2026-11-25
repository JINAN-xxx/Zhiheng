import runpy
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = SOURCE_ROOT / 'tools/check_core_wheel.py'
BACKEND_ROOT = SOURCE_ROOT / 'backend'
MIGRATION_ROOT = BACKEND_ROOT / 'migrations'
DATABASE_MODULE = 'zhiheng/infrastructure/database.py'
MIGRATION_PREFIX = 'zhiheng_platform-0.1.0.data/data/share/zhiheng/migrations/'
MIGRATION_FILES = {
    MIGRATION_PREFIX + path.relative_to(MIGRATION_ROOT).as_posix(): path
    for path in [MIGRATION_ROOT / 'env.py', *sorted((MIGRATION_ROOT / 'versions').glob('*.py'))]
}
DIST_INFO = 'zhiheng_platform-0.1.0.dist-info'
METADATA_MEMBER = DIST_INFO + '/METADATA'
LICENSE_METADATA = (
    'Metadata-Version: 2.4\nName: zhiheng-platform\nVersion: 0.1.0\n'
    'License-Expression: MIT\nLicense-File: LICENSE\nLicense-File: THIRD_PARTY_NOTICES.md\n'
)
REQUIRED_MEMBERS = (
    'zhiheng/cli/main.py',
    'zhiheng/__init__.py',
    'zhiheng/py.typed',
    DATABASE_MODULE,
    'zhiheng/shared/references.py',
    'zhiheng/application/developer/management.py',
    'zhiheng/modules/billing/public.py',
    'zhiheng/modules/billing/contracts.py',
    'zhiheng/modules/billing/orm.py',
    'zhiheng/modules/billing/service.py',
    'zhiheng/modules/billing/queries.py',
    'zhiheng/modules/billing/projections.py',
    'zhiheng/modules/billing/rates.py',
    'zhiheng/modules/billing/commands.py',
    'zhiheng/modules/billing/pricing.py',
    'zhiheng/modules/billing/usage.py',
    'zhiheng/modules/billing/ports.py',
    'zhiheng/modules/billing/gate.py',
    'zhiheng/application/billing/management.py',
    'zhiheng/api/console/billing.py',
    'zhiheng/api/main.py',
    'zhiheng/api/common/openapi.py',
    'zhiheng/api/common/examples.py',
    'zhiheng/api/console/router.py',
    'zhiheng/api/resources/router.py',
    'zhiheng/api/inference/router.py',
    'zhiheng/api/inference/schemas.py',
    'zhiheng/api/inference/streaming.py',
    'zhiheng/api/resources/schemas/files.py',
    'zhiheng/api/resources/schemas/knowledge.py',
    'zhiheng/api/resources/schemas/skills.py',
    'zhiheng/api/resources/schemas/operations.py',
    'zhiheng/application/developer/resources.py',
    'zhiheng/application/identity/__init__.py',
    'zhiheng/application/identity/accounts.py',
    'zhiheng/modules/skills/public.py',
    'zhiheng/modules/skills/package.py',
    'zhiheng/modules/skills/validation.py',
    'zhiheng/api/console/skills.py',
    'zhiheng/modules/agents/public.py',
    'zhiheng/modules/agents/LICENSE',
    'zhiheng/application/agents/runs.py',
    'zhiheng/application/agents/execution.py',
    'zhiheng/modules/agents/execution.py',
    'zhiheng/shared/agent.py',
    'zhiheng/modules/models/summary.py',
    'zhiheng/modules/work/history.py',
    'zhiheng/api/console/agents.py',
    'zhiheng/api/inference/agents.py',
    'zhiheng/modules/work/public.py',
    'zhiheng/application/work/runs.py',
    'zhiheng/application/work/agent.py',
    'zhiheng/application/work/context.py',
    'zhiheng/application/work/execution.py',
    'zhiheng/application/work/snapshots.py',
    'zhiheng/application/work/tools.py',
    'zhiheng/modules/work/agent.py',
    'zhiheng/modules/work/agent_contracts.py',
    'zhiheng/modules/work/agent_ports.py',
    'zhiheng/infrastructure/agents/adapters/workspace.py',
    'zhiheng/infrastructure/sandbox/__init__.py',
    'zhiheng/infrastructure/sandbox/contracts.py',
    'zhiheng/infrastructure/sandbox/paths.py',
    'zhiheng/infrastructure/sandbox/adapters/opensandbox.py',
    'zhiheng/bootstrap/sandbox.py',
    'zhiheng/application/files/objects.py',
    'zhiheng/modules/files/workspace_contracts.py',
    'zhiheng/modules/files/adapters/workspace.py',
    'zhiheng/modules/files/adapters/workspace_worker.py',
    'zhiheng/application/chat/agent.py',
    'zhiheng/application/models/trials.py',
    'zhiheng/modules/models/capacity.py',
    'zhiheng/modules/models/connections.py',
    'zhiheng/modules/models/catalog.py',
    'zhiheng/modules/models/bindings.py',
    'zhiheng/modules/models/configuration.py',
    'zhiheng/modules/models/rates.py',
    'zhiheng/modules/models/projections.py',
    'zhiheng/bootstrap/preflight.py',
    'zhiheng/modules/files/parsing.py',
    'zhiheng/modules/files/parsing_contracts.py',
    'zhiheng/modules/files/parsing_orm.py',
    'zhiheng/modules/files/adapters/native.py',
    'zhiheng/modules/files/adapters/mineru.py',
    'zhiheng/modules/files/adapters/mineru_cloud.py',
    'zhiheng/infrastructure/agents/adapters/agent.py',
    'zhiheng/modules/chat/execution.py',
    'zhiheng/infrastructure/agents/adapters/checkpoints.py',
    'zhiheng/application/execution/__init__.py',
    'zhiheng/application/execution/admission.py',
    'zhiheng/application/execution/authorization.py',
    'zhiheng/application/execution/executor.py',
    'zhiheng/application/execution/lifecycle.py',
    'zhiheng/application/execution/recovery.py',
    'zhiheng/api/console/work.py',
    'zhiheng/api/console/files.py',
    'zhiheng/modules/files/public.py',
    'zhiheng/modules/files/jobs.py',
    'zhiheng/modules/files/service.py',
    'zhiheng/modules/files/queries.py',
    'zhiheng/modules/files/lifecycle.py',
    'zhiheng/modules/files/references.py',
    'zhiheng/modules/files/metadata.py',
    'zhiheng/infrastructure/file_storage.py',
    'zhiheng/bootstrap/file_storage.py',
    'zhiheng/cli/files.py',
    'zhiheng/cli/legacy_files.py',
    'zhiheng/modules/files/storage_migration.py',
    'zhiheng/modules/files/adapters/native_worker.py',
    'zhiheng/modules/knowledge/adapters/milvus.py',
    'zhiheng/modules/knowledge/adapters/milvus_rpc.py',
    'zhiheng/modules/knowledge/build.py',
    'zhiheng/modules/knowledge/service.py',
    'zhiheng/modules/knowledge/materials.py',
    'zhiheng/modules/knowledge/build_admission.py',
    'zhiheng/modules/knowledge/migration.py',
    'zhiheng/modules/knowledge/references.py',
    'zhiheng/modules/knowledge/values.py',
    'zhiheng/modules/knowledge/retrieval.py',
    'zhiheng/modules/knowledge/generations.py',
    'zhiheng/application/knowledge/preparation.py',
    'zhiheng/application/knowledge/jobs.py',
    'zhiheng/api/console/knowledge.py',
    'zhiheng/api/console/knowledge_graph.py',
    'zhiheng/application/knowledge/graph/jobs.py',
    'zhiheng/application/knowledge/graph/runs.py',
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
    *MIGRATION_FILES,
)


@pytest.fixture
def verify_wheel() -> Callable[[Path], Path]:
    namespace = runpy.run_path(str(CHECKER_PATH))
    return cast('Callable[[Path], Path]', namespace['verify_core_wheel'])


def write_wheel(
    directory: Path,
    members: tuple[str, ...],
    *,
    contents: dict[str, str | bytes] | None = None,
    omit: tuple[str, ...] = (),
) -> Path:
    entries: dict[str, str | bytes] = dict.fromkeys(members, '')
    if DATABASE_MODULE in entries:
        entries[DATABASE_MODULE] = (BACKEND_ROOT / 'src' / DATABASE_MODULE).read_bytes()
    for member in set(members) & MIGRATION_FILES.keys():
        entries[member] = MIGRATION_FILES[member].read_bytes()
    entries[METADATA_MEMBER] = LICENSE_METADATA
    for filename in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        entries[f'{DIST_INFO}/licenses/{filename}'] = (SOURCE_ROOT / filename).read_bytes()
    entries.update(contents or {})
    path = directory / 'zhiheng_platform-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for member, content in entries.items():
            if member not in omit:
                archive.writestr(member, content)
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
        'zhiheng/modules/models/adapters/chat_agent.py',
        'zhiheng/modules/agents/context.py',
        'zhiheng/modules/agents/history.py',
        'zhiheng/modules/chat/summary_policy.py',
        'zhiheng/modules/chat/adapters/agent.py',
        'zhiheng/modules/chat/adapters/checkpoints.py',
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


@pytest.mark.parametrize('member', MIGRATION_FILES)
def test_requires_all_database_migration_revisions(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, member: str
) -> None:
    write_wheel(
        tmp_path,
        REQUIRED_MEMBERS,
        omit=(member,),
    )
    with pytest.raises(ValueError, match='migration'):
        verify_wheel(tmp_path)


def test_rejects_stale_or_extra_migration_resources(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    write_wheel(tmp_path, REQUIRED_MEMBERS, contents={MIGRATION_PREFIX + 'env.py': '# stale migration environment'})
    with pytest.raises(ValueError, match='migration content differs'):
        verify_wheel(tmp_path)

    extra = MIGRATION_PREFIX + 'versions/deleted_migration.py'
    write_wheel(tmp_path, (*REQUIRED_MEMBERS, extra))
    with pytest.raises(ValueError, match='migration inventory differs'):
        verify_wheel(tmp_path)


def test_rejects_wheel_with_mismatched_runtime_schema_version(
    verify_wheel: Callable[[Path], Path], tmp_path: Path
) -> None:
    write_wheel(tmp_path, REQUIRED_MEMBERS, contents={DATABASE_MODULE: "SCHEMA_REVISION = 'wrong'"})
    with pytest.raises(ValueError, match='Alembic and runtime schema revisions differ'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize('member', ['zhiheng/modules/billing/public.py', 'zhiheng/api/console/billing.py'])
def test_requires_platform_billing_resources(verify_wheel: Callable[[Path], Path], tmp_path: Path, member: str) -> None:
    write_wheel(tmp_path, tuple(name for name in REQUIRED_MEMBERS if name != member))
    with pytest.raises(ValueError, match='missing'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize('member', ['zhiheng/shared/references.py', 'zhiheng/application/developer/management.py'])
def test_requires_resource_lifecycle_services(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, member: str
) -> None:
    write_wheel(tmp_path, tuple(name for name in REQUIRED_MEMBERS if name != member))
    with pytest.raises(ValueError, match='missing'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize(
    'member',
    [
        'zhiheng/application/work/tools.py',
        'zhiheng/infrastructure/agents/adapters/workspace.py',
        'zhiheng/infrastructure/sandbox/adapters/opensandbox.py',
        'zhiheng/modules/files/adapters/workspace_worker.py',
    ],
)
def test_requires_workspace_execution_resources(
    verify_wheel: Callable[[Path], Path],
    tmp_path: Path,
    member: str,
) -> None:
    write_wheel(tmp_path, tuple(name for name in REQUIRED_MEMBERS if name != member))
    with pytest.raises(ValueError, match='missing'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize('filename', ['LICENSE', 'THIRD_PARTY_NOTICES.md'])
@pytest.mark.parametrize('changed', [False, True])
def test_rejects_missing_or_changed_project_license(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, filename: str, changed: bool
) -> None:
    member = f'{DIST_INFO}/licenses/{filename}'
    write_wheel(
        tmp_path,
        REQUIRED_MEMBERS,
        contents={member: b'replaced license'} if changed else None,
        omit=() if changed else (member,),
    )
    with pytest.raises(ValueError, match='license content'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize(
    'metadata',
    [
        LICENSE_METADATA.replace('License-Expression: MIT', 'License-Expression: Apache-2.0'),
        LICENSE_METADATA.replace('License-File: THIRD_PARTY_NOTICES.md\n', ''),
        LICENSE_METADATA + 'License-File: UNDECLARED_NOTICE\n',
    ],
)
def test_rejects_incorrect_license_metadata(
    verify_wheel: Callable[[Path], Path], tmp_path: Path, metadata: str
) -> None:
    write_wheel(tmp_path, REQUIRED_MEMBERS, contents={METADATA_MEMBER: metadata})
    with pytest.raises(ValueError, match='METADATA'):
        verify_wheel(tmp_path)


def test_requires_project_metadata(verify_wheel: Callable[[Path], Path], tmp_path: Path) -> None:
    write_wheel(tmp_path, REQUIRED_MEMBERS, omit=(METADATA_MEMBER,))
    with pytest.raises(ValueError, match='METADATA'):
        verify_wheel(tmp_path)


@pytest.mark.parametrize('filename', ['LICENSE', 'THIRD_PARTY_NOTICES.md'])
def test_rejects_backend_license_copy_drift(tmp_path: Path, filename: str) -> None:
    namespace = runpy.run_path(str(CHECKER_PATH))
    read_licenses = cast('Callable[[Path], dict[str, bytes]]', namespace['source_license_files'])
    (tmp_path / 'backend').mkdir()
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        (tmp_path / name).write_bytes((SOURCE_ROOT / name).read_bytes())
        (tmp_path / 'backend' / name).write_bytes((SOURCE_ROOT / name).read_bytes())
    (tmp_path / 'backend' / filename).write_bytes(b'changed copyright')
    with pytest.raises(ValueError, match='copy differs'):
        read_licenses(tmp_path)


def fixture_source_package(directory: Path) -> None:
    for member in REQUIRED_MEMBERS:
        if member.startswith('zhiheng/') and member.endswith('.py'):
            path = directory / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((BACKEND_ROOT / 'src' / member).read_bytes() if member == DATABASE_MODULE else b'')
    shutil.copytree(MIGRATION_ROOT, directory.parent / 'migrations', ignore=shutil.ignore_patterns('__pycache__'))


def test_accepts_wheel_with_exact_source_files_and_contents(tmp_path: Path) -> None:
    source = tmp_path / 'src'
    fixture_source_package(source)
    wheel = write_wheel(tmp_path, REQUIRED_MEMBERS)
    verifier = cast('Callable[..., Path]', runpy.run_path(str(CHECKER_PATH))['verify_core_wheel'])
    assert verifier(tmp_path, source_root=source) == wheel


def test_rejects_diverged_alembic_migration_graph(tmp_path: Path) -> None:
    source = tmp_path / 'src'
    fixture_source_package(source)
    (tmp_path / 'migrations/versions/orphan.py').write_text(
        "revision = 'orphan'\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n"
    )
    write_wheel(tmp_path, REQUIRED_MEMBERS)
    verifier = cast('Callable[..., Path]', runpy.run_path(str(CHECKER_PATH))['verify_core_wheel'])
    with pytest.raises(ValueError, match='Expected one Alembic migration head'):
        verifier(tmp_path, source_root=source)


def test_rejects_source_with_mismatched_runtime_schema_version(tmp_path: Path) -> None:
    source = tmp_path / 'src'
    fixture_source_package(source)
    (source / DATABASE_MODULE).write_text("SCHEMA_REVISION = 'wrong'")
    write_wheel(tmp_path, REQUIRED_MEMBERS)
    verifier = cast('Callable[..., Path]', runpy.run_path(str(CHECKER_PATH))['verify_core_wheel'])
    with pytest.raises(ValueError, match='Alembic and runtime schema revisions differ'):
        verifier(tmp_path, source_root=source)


def test_rejects_deleted_module_left_in_build_cache(tmp_path: Path) -> None:
    source = tmp_path / 'src'
    fixture_source_package(source)
    write_wheel(tmp_path, (*REQUIRED_MEMBERS, 'zhiheng/obsolete_cached_module.py'))
    verifier = cast('Callable[..., Path]', runpy.run_path(str(CHECKER_PATH))['verify_core_wheel'])
    with pytest.raises(ValueError, match='unexpected'):
        verifier(tmp_path, source_root=source)


def test_rejects_missing_new_source_file_in_wheel(tmp_path: Path) -> None:
    source = tmp_path / 'src'
    fixture_source_package(source)
    (source / 'zhiheng/new_module.py').write_text('# new source')
    write_wheel(tmp_path, REQUIRED_MEMBERS)
    verifier = cast('Callable[..., Path]', runpy.run_path(str(CHECKER_PATH))['verify_core_wheel'])
    with pytest.raises(ValueError, match='missing'):
        verifier(tmp_path, source_root=source)


def test_rejects_stale_contents_under_an_existing_module_name(tmp_path: Path) -> None:
    source = tmp_path / 'src'
    fixture_source_package(source)
    (source / 'zhiheng/cli/main.py').write_text('# updated source')
    write_wheel(tmp_path, REQUIRED_MEMBERS)
    verifier = cast('Callable[..., Path]', runpy.run_path(str(CHECKER_PATH))['verify_core_wheel'])
    with pytest.raises(ValueError, match='content differs'):
        verifier(tmp_path, source_root=source)
