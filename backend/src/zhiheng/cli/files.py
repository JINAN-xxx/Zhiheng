"""Explicit operator-controlled storage migration with the existing database as authority."""

from pathlib import Path

from zhiheng.bootstrap.file_storage import create_file_storage
from zhiheng.bootstrap.settings import Settings
from zhiheng.cli.legacy_files import LegacyFileSource
from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileStorageMigration, StorageMigrationReport


async def migrate_files(
    settings: Settings,
    source_root: Path,
    *,
    verify_only: bool = False,
) -> StorageMigrationReport:
    source = LegacyFileSource(source_root)
    target = create_file_storage(settings)
    database = Database(settings.database_url.get_secret_value())
    try:
        await database.require_schema()
        return await FileStorageMigration(database.sessions).run(source, target, verify_only=verify_only)
    finally:
        await database.close()
