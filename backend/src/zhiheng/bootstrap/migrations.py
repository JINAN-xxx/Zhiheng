import sysconfig
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.bootstrap.settings import Settings


def migration_directory() -> Path:
    source = Path(__file__).resolve().parents[3] / 'migrations'
    installed = Path(sysconfig.get_path('data')) / 'share/zhiheng/migrations'
    directory = source if source.is_dir() else installed
    if not (directory / 'env.py').is_file():
        raise RuntimeError('Packaged database migrations are missing')
    return directory


async def migrate(settings: Settings) -> None:
    config = Config()
    config.attributes['target_metadata'] = schema_metadata()
    config.set_main_option('script_location', str(migration_directory()))

    def upgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')

    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql('SELECT pg_advisory_xact_lock(784246991)')
            await connection.run_sync(upgrade)
    finally:
        await engine.dispose()
