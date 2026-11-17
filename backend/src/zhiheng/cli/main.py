# ruff: noqa: PLC0415 -- Each CLI command imports only its own runtime dependencies.
import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from zhiheng.bootstrap.settings import Settings
from zhiheng.shared.errors import AppError


async def create_admin(settings: Settings, username: str, password: str) -> None:
    from zhiheng.application.identity.accounts import AccountService
    from zhiheng.infrastructure.database import Database
    from zhiheng.modules.identity.public import IdentityService
    from zhiheng.modules.skills.public import SkillService

    database = Database(settings.database_url.get_secret_value())
    try:
        await database.require_schema()
        async with database.sessions.begin() as session:
            await AccountService(IdentityService(), SkillService()).create_user(
                session, username=username, password=password, is_admin=True
            )
    finally:
        await database.close()


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='zhiheng', description='智衡 AI 平台独立运行入口')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='启动 API')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=19090)
    serve.add_argument('--reload', action='store_true', help='源码开发时自动重载API')
    commands.add_parser('worker', help='启动持久任务 worker')
    commands.add_parser('migrate', help='初始化或升级新平台数据库')
    admin = commands.add_parser('create-admin', help='显式创建管理员')
    admin.add_argument('--username', required=True)
    admin.add_argument('--password-stdin', action='store_true')
    files = commands.add_parser('migrate-files', help='离线迁移或校验受管文件，保留原文件与全部权限')
    files.add_argument('--source-root', type=Path, required=True, help='一次性迁移所读取的旧文件目录')
    files.add_argument('--verify-only', action='store_true', help='只校验目标存储，不写入对象')
    files.add_argument('--offline', action='store_true', required=True, help='确认已停止 API 与 Worker 的文件读写')
    return parser.parse_args(argv)


def _source_preflight(settings: Settings, command: str) -> None:
    requirements = os.getenv('ZHIHENG_SOURCE_REQUIREMENTS')
    if requirements:
        from zhiheng.bootstrap.preflight import require_dependencies, require_source_schema

        require_dependencies(Path(requirements))
        if command in {'serve', 'worker'}:
            asyncio.run(require_source_schema(settings))


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        fields = ', '.join('.'.join(str(part) for part in item['loc']) for item in exc.errors())
        print(f'配置无效，请检查：{fields}', file=sys.stderr)
        return 2
    try:
        _source_preflight(settings, args.command)
        if args.command == 'serve':
            import uvicorn

            from zhiheng.api.main import create_app
            from zhiheng.api.request_logging import uvicorn_log_config

            if args.reload:
                uvicorn.run(
                    'zhiheng.api.main:create_configured_app',
                    host=args.host,
                    port=args.port,
                    factory=True,
                    reload=True,
                    reload_dirs=[str(Path(__file__).resolve().parents[1])],
                    proxy_headers=False,
                    log_config=uvicorn_log_config(),
                )
            else:
                uvicorn.run(
                    create_app(settings),
                    host=args.host,
                    port=args.port,
                    proxy_headers=False,
                    log_config=uvicorn_log_config(),
                )
        elif args.command == 'worker':
            from zhiheng.worker.main import run_worker

            asyncio.run(run_worker(settings))
        elif args.command == 'migrate':
            from zhiheng.bootstrap.migrations import migrate

            asyncio.run(migrate(settings))
        elif args.command == 'migrate-files':
            from zhiheng.cli.files import migrate_files

            report = asyncio.run(migrate_files(settings, args.source_root, verify_only=args.verify_only))
            print(
                f'文件校验完成：{report.scanned} 个，新增复制 {report.copied} 个，'
                f'已存在并通过校验 {report.existing} 个，共 {report.total_bytes} 字节；'
                '原文件和数据库元数据均保留。'
            )
        else:
            password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('管理员密码：')
            asyncio.run(create_admin(settings, args.username, password))
            print('管理员已创建')
    except asyncio.CancelledError:
        return 0
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'命令执行失败（{type(exc).__name__}），请检查新平台配置与服务状态', file=sys.stderr)
        return 1
    return 0
