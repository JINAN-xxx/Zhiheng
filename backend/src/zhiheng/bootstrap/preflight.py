"""Read-only source-overlay checks, before importing application or provider SDKs."""

from importlib import metadata
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.database import Database
from zhiheng.shared.errors import AppError


def require_dependencies(path: Path) -> None:
    mismatches = []
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        raise AppError('runtime_requirements_unavailable', '无法读取源码依赖清单，请检查源码挂载', 503) from error
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as error:
            raise AppError('runtime_requirements_invalid', '源码依赖清单无效，请先构建后端镜像', 503) from error
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            version = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            version = None
        if version is None or version not in requirement.specifier:
            mismatches.append(requirement.name)
    if mismatches:
        raise AppError(
            'runtime_dependencies_mismatch', '源码与后端镜像依赖不匹配，请先重新构建镜像：' + ', '.join(mismatches), 503
        )


async def require_source_schema(settings: Settings) -> None:
    database = Database(settings.database_url.get_secret_value())
    try:
        await database.require_schema()
    finally:
        await database.close()
