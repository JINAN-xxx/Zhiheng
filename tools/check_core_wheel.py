"""Verify runtime files, source inventory, migrations and licenses in a platform wheel."""

import argparse
import zipfile
from email.parser import BytesParser
from pathlib import Path

REQUIRED_FILES = frozenset(
    {
        "zhiheng/cli/main.py",
        "zhiheng/__init__.py",
        "zhiheng/py.typed",
        "zhiheng/shared/references.py",
        "zhiheng/application/developer/management.py",
        "zhiheng/modules/billing/public.py",
        "zhiheng/modules/billing/contracts.py",
        "zhiheng/modules/billing/orm.py",
        "zhiheng/modules/billing/service.py",
        "zhiheng/modules/billing/queries.py",
        "zhiheng/modules/billing/projections.py",
        "zhiheng/modules/billing/rates.py",
        "zhiheng/modules/billing/commands.py",
        "zhiheng/modules/billing/pricing.py",
        "zhiheng/modules/billing/usage.py",
        "zhiheng/modules/billing/ports.py",
        "zhiheng/modules/billing/gate.py",
        "zhiheng/application/billing/management.py",
        "zhiheng/api/console/billing.py",
        "zhiheng/api/main.py",
        "zhiheng/api/common/openapi.py",
        "zhiheng/api/common/examples.py",
        "zhiheng/api/console/router.py",
        "zhiheng/api/resources/router.py",
        "zhiheng/api/inference/router.py",
        "zhiheng/api/inference/schemas.py",
        "zhiheng/api/inference/streaming.py",
        "zhiheng/api/resources/schemas/files.py",
        "zhiheng/api/resources/schemas/knowledge.py",
        "zhiheng/api/resources/schemas/skills.py",
        "zhiheng/api/resources/schemas/operations.py",
        "zhiheng/application/developer/resources.py",
        "zhiheng/application/identity/__init__.py",
        "zhiheng/application/identity/accounts.py",
        "zhiheng/modules/skills/public.py",
        "zhiheng/modules/skills/package.py",
        "zhiheng/modules/skills/validation.py",
        "zhiheng/api/console/skills.py",
        "zhiheng/modules/agents/public.py",
        "zhiheng/modules/agents/LICENSE",
        "zhiheng/application/agents/runs.py",
        "zhiheng/application/agents/execution.py",
        "zhiheng/modules/agents/execution.py",
        "zhiheng/shared/agent.py",
        "zhiheng/modules/models/summary.py",
        "zhiheng/modules/work/history.py",
        "zhiheng/api/console/agents.py",
        "zhiheng/api/inference/agents.py",
        "zhiheng/modules/work/public.py",
        "zhiheng/application/work/runs.py",
        "zhiheng/application/work/agent.py",
        "zhiheng/application/work/context.py",
        "zhiheng/application/work/execution.py",
        "zhiheng/application/work/snapshots.py",
        "zhiheng/application/work/tools.py",
        "zhiheng/modules/work/agent.py",
        "zhiheng/modules/work/agent_contracts.py",
        "zhiheng/modules/work/agent_ports.py",
        "zhiheng/infrastructure/agents/adapters/workspace.py",
        "zhiheng/infrastructure/sandbox/__init__.py",
        "zhiheng/infrastructure/sandbox/contracts.py",
        "zhiheng/infrastructure/sandbox/paths.py",
        "zhiheng/infrastructure/sandbox/adapters/opensandbox.py",
        "zhiheng/bootstrap/sandbox.py",
        "zhiheng/application/files/objects.py",
        "zhiheng/modules/files/workspace_contracts.py",
        "zhiheng/modules/files/adapters/workspace.py",
        "zhiheng/modules/files/adapters/workspace_worker.py",
        "zhiheng/application/chat/agent.py",
        "zhiheng/application/models/trials.py",
        "zhiheng/modules/models/capacity.py",
        "zhiheng/modules/models/connections.py",
        "zhiheng/modules/models/catalog.py",
        "zhiheng/modules/models/bindings.py",
        "zhiheng/modules/models/configuration.py",
        "zhiheng/modules/models/rates.py",
        "zhiheng/modules/models/projections.py",
        "zhiheng/bootstrap/preflight.py",
        "zhiheng/modules/files/parsing.py",
        "zhiheng/modules/files/parsing_contracts.py",
        "zhiheng/modules/files/parsing_orm.py",
        "zhiheng/modules/files/adapters/native.py",
        "zhiheng/modules/files/adapters/mineru.py",
        "zhiheng/modules/files/adapters/mineru_cloud.py",
        "zhiheng/infrastructure/agents/adapters/agent.py",
        "zhiheng/modules/chat/execution.py",
        "zhiheng/infrastructure/agents/adapters/checkpoints.py",
        "zhiheng/application/execution/__init__.py",
        "zhiheng/application/execution/admission.py",
        "zhiheng/application/execution/authorization.py",
        "zhiheng/application/execution/executor.py",
        "zhiheng/application/execution/lifecycle.py",
        "zhiheng/application/execution/recovery.py",
        "zhiheng/api/console/work.py",
        "zhiheng/api/console/files.py",
        "zhiheng/modules/files/public.py",
        "zhiheng/modules/files/jobs.py",
        "zhiheng/modules/files/service.py",
        "zhiheng/modules/files/queries.py",
        "zhiheng/modules/files/lifecycle.py",
        "zhiheng/modules/files/references.py",
        "zhiheng/modules/files/metadata.py",
        "zhiheng/infrastructure/file_storage.py",
        "zhiheng/bootstrap/file_storage.py",
        "zhiheng/cli/files.py",
        "zhiheng/cli/legacy_files.py",
        "zhiheng/modules/files/storage_migration.py",
        "zhiheng/modules/files/adapters/native_worker.py",
        "zhiheng/modules/knowledge/adapters/milvus.py",
        "zhiheng/modules/knowledge/adapters/milvus_rpc.py",
        "zhiheng/modules/knowledge/build.py",
        "zhiheng/modules/knowledge/service.py",
        "zhiheng/modules/knowledge/materials.py",
        "zhiheng/modules/knowledge/build_admission.py",
        "zhiheng/modules/knowledge/migration.py",
        "zhiheng/modules/knowledge/references.py",
        "zhiheng/modules/knowledge/values.py",
        "zhiheng/modules/knowledge/retrieval.py",
        "zhiheng/modules/knowledge/generations.py",
        "zhiheng/application/knowledge/preparation.py",
        "zhiheng/application/knowledge/jobs.py",
        "zhiheng/api/console/knowledge.py",
        "zhiheng/api/console/knowledge_graph.py",
        "zhiheng/application/knowledge/graph/jobs.py",
        "zhiheng/application/knowledge/graph/runs.py",
        "zhiheng/modules/knowledge/adapters/lightrag/LICENSE",
        "zhiheng/modules/knowledge/adapters/lightrag/tokenizer.py",
        "zhiheng/modules/knowledge/adapters/lightrag/TIKTOKEN_LICENSE",
        "zhiheng/modules/knowledge/adapters/lightrag/tokenizer_spec.json",
        "zhiheng/modules/knowledge/adapters/lightrag/o200k_base.tiktoken",
        "zhiheng/modules/knowledge/adapters/lightrag/__init__.py",
        "zhiheng/modules/knowledge/adapters/lightrag/policy.py",
        "zhiheng/modules/knowledge/adapters/lightrag/catalog.json",
        "zhiheng/modules/knowledge/adapters/lightrag/factory.py",
        "zhiheng/modules/knowledge/adapters/lightrag/gc.py",
        "zhiheng/modules/knowledge/adapters/lightrag/managed.py",
        "zhiheng/modules/knowledge/adapters/lightrag/owned_loop.py",
        "zhiheng/modules/knowledge/adapters/lightrag/postgres.py",
        "zhiheng/modules/knowledge/adapters/lightrag/registry.py",
        "zhiheng/modules/knowledge/adapters/lightrag/results.py",
        "zhiheng/modules/knowledge/adapters/lightrag/schema.py",
        "zhiheng/modules/knowledge/adapters/lightrag/sdk.py",
        "zhiheng/modules/knowledge/adapters/lightrag/sdk_hashes.json",
        "zhiheng/modules/knowledge/adapters/lightrag/storage.py",
        "zhiheng/modules/knowledge/adapters/lightrag/transport.py",
        "zhiheng/modules/knowledge/adapters/lightrag/vector.py",
        "zhiheng/modules/knowledge/graph/contracts.py",
        "zhiheng/modules/knowledge/graph/lifecycle.py",
        "zhiheng/modules/knowledge/graph/orm.py",
        "zhiheng/modules/knowledge/graph/service.py",
        "zhiheng/modules/knowledge/graph/store.py",
        "zhiheng/worker/graph_runner.py",
    }
)


LICENSE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def source_license_files(root: Path) -> dict[str, bytes]:
    contents = {}
    for filename in LICENSE_FILES:
        try:
            content = (root / filename).read_bytes()
            backend_content = (root / "backend" / filename).read_bytes()
        except OSError as error:
            raise ValueError(
                f"Required source license file is unavailable: {filename}"
            ) from error
        if backend_content != content:
            raise ValueError(
                f"Backend license copy differs from the project root: {filename}"
            )
        contents[filename] = content
    return contents


def verify_wheel_licenses(archive: zipfile.ZipFile, names: set[str]) -> None:
    metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
    if len(metadata_files) != 1:
        raise ValueError("Core wheel must contain one project METADATA file")
    metadata_name = metadata_files[0]
    metadata = BytesParser().parsebytes(archive.read(metadata_name))
    if metadata.get("License-Expression") != "MIT":
        raise ValueError("Core wheel METADATA must declare License-Expression: MIT")
    if set(metadata.get_all("License-File", [])) != set(LICENSE_FILES):
        raise ValueError(
            "Core wheel METADATA must declare the project license and third-party notices"
        )
    prefix = metadata_name.rsplit("/", 1)[0] + "/licenses/"
    for filename, content in source_license_files(
        Path(__file__).resolve().parents[1]
    ).items():
        member = prefix + filename
        if member not in names or archive.read(member) != content:
            raise ValueError(
                f"Core wheel license content is missing or differs from the project root: {filename}"
            )


def verify_source_inventory(archive: zipfile.ZipFile, source_root: Path) -> None:
    """A deleted source must not survive through setuptools' reused build/lib cache."""
    package = source_root / "zhiheng"
    if not package.is_dir():
        raise ValueError(f"Python source package is unavailable: {package}")
    expected = {
        path.relative_to(source_root).as_posix(): path for path in package.rglob("*.py")
    }
    actual = {
        name
        for name in archive.namelist()
        if name.startswith("zhiheng/") and name.endswith(".py")
    }
    if actual != set(expected):
        raise ValueError(
            "Wheel Python source inventory differs: "
            f"unexpected={sorted(actual - set(expected))}, missing={sorted(set(expected) - actual)}"
        )
    changed = [
        name
        for name, path in expected.items()
        if archive.read(name) != path.read_bytes()
    ]
    if changed:
        raise ValueError(f"Wheel Python source content differs: {sorted(changed)}")


def verify_core_wheel(directory: Path, *, source_root: Path | None = None) -> Path:
    wheels = sorted(directory.glob("zhiheng_platform-*.whl"))
    if len(wheels) != 1:
        raise ValueError(
            f"Expected one platform wheel in {directory}, found {len(wheels)}"
        )
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    missing = REQUIRED_FILES - names
    if missing:
        raise ValueError(f"Core wheel is missing: {', '.join(sorted(missing))}")
    with zipfile.ZipFile(wheel) as archive:
        verify_wheel_licenses(archive, names)
        if source_root is not None:
            verify_source_inventory(archive, source_root)
    migrations = (
        "env.py",
        "versions/0001_core.py",
        "versions/0002_developer.py",
        "versions/0003_skills.py",
        "versions/0004_agents.py",
        "versions/0005_work.py",
        "versions/0006_files.py",
        "versions/0007_knowledge.py",
        "versions/0008_graph.py",
        "versions/0009_message_actions.py",
        "versions/0010_admin_console.py",
        "versions/0011_builtin_admin_role.py",
        "versions/0012_platform_credits.py",
        "versions/0013_skill_revision_history.py",
        "versions/0014_personal_skill_ownership.py",
        "versions/0015_resource_api_access.py",
        "versions/0016_file_purpose.py",
        "versions/0017_chat_context_memory.py",
        "versions/0018_file_job_cursor.py",
        "versions/0019_developer_key_deletion.py",
        "versions/0020_knowledge_library.py",
        "versions/0021_knowledge_settings.py",
        "versions/0022_knowledge_invitations.py",
        "versions/0023_knowledge_link_only.py",
        "versions/0024_knowledge_rerank_required.py",
        "versions/0025_product_closure.py",
        "versions/0026_model_output_range.py",
        "versions/0027_default_work_skills.py",
        "versions/0028_document_parsing.py",
        "versions/0029_application_governance.py",
        "versions/0030_assistant_conversations.py",
        "versions/0031_assistant_turn_versions.py",
        "versions/0032_skill_market.py",
        "versions/0033_assistant_session_actions.py",
        "versions/0034_permission_tree.py",
        "versions/0035_chat_checkpoints.py",
        "versions/0036_chat_runtime_cleanup.py",
        "versions/0037_model_capacity_note.py",
        "versions/0038_skill_receipt_payload.py",
        "versions/0039_retired_runtime_fields.py",
        "versions/0040_assistant_agents.py",
        "versions/0041_work_agent.py",
        "versions/0041_assistant_attachments.py",
        "versions/0042_workspace_merge.py",
        "versions/0043_workspace_execution.py",
        "versions/0044_key_object_grants.py",
    )
    if any(
        not any(
            name.endswith("/share/zhiheng/migrations/" + migration) for name in names
        )
        for migration in migrations
    ):
        raise ValueError("Core wheel is missing required database migration resources")
    if any(
        name
        in {
            "zhiheng/modules/models/adapters/chat_agent.py",
            "zhiheng/modules/agents/history.py",
            "zhiheng/modules/chat/summary_policy.py",
            "zhiheng/modules/chat/adapters/agent.py",
            "zhiheng/modules/chat/adapters/checkpoints.py",
            "zhiheng/modules/agents/context.py",
            "zhiheng/modules/knowledge/adapters/native.py",
            "zhiheng/modules/knowledge/adapters/native_worker.py",
            "zhiheng/modules/knowledge/adapters/mineru.py",
            "zhiheng/modules/knowledge/adapters/mineru_cloud.py",
        }
        or name.startswith(("plugins/", "config/", "legacy/", "cli/"))
        or any(part.startswith(".env") for part in Path(name).parts)
        for name in names
    ):
        raise ValueError("Core wheel includes legacy runtime or environment files")
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Compare every packaged Python file with this src directory",
    )
    args = parser.parse_args()
    print(
        f"Core wheel verified: {verify_core_wheel(args.directory, source_root=args.source_root)}"
    )


if __name__ == "__main__":
    main()
