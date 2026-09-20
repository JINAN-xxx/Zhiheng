"""只依赖 Python 标准库的完整平台部署入口。"""

import argparse
import fcntl
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "PLATFORM_DEPLOYMENT_VERSION": "1",
    "PLATFORM_COMPOSE_PROJECT": "zhiheng",
    "PLATFORM_PORT": "12580",
    "PLATFORM_BIND_ADDRESS": "0.0.0.0",
    "PLATFORM_DATA_DIR": "./data",
    "PLATFORM_BACKEND_IMAGE": "zhiheng-backend:local",
    "AI_PUBLIC_API_ENABLED": "true",
    "AI_API_KEY_PEPPER_CURRENT_VERSION": "v1",
}
SECRET_KEYS = (
    "RUOYI_DB_PASSWORD",
    "RUOYI_REDIS_PASSWORD",
    "JWT_SECRET_KEY",
    "LITELLM_DB_PASSWORD",
    "LITELLM_REDIS_PASSWORD",
    "LITELLM_MASTER_KEY",
    "LITELLM_SALT_KEY",
    "LITELLM_RUNTIME_KEY",
    "AI_API_KEY_PEPPER_V1",
)


class DeploymentError(Exception):
    """可以直接显示给操作者的部署错误。"""


def read_environment(path: Path) -> dict[str, str]:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise DeploymentError("环境文件必须是普通文件，不能是符号链接")
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, raw = line.partition("=")
        name = name.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise DeploymentError("环境文件包含无效配置行")
        try:
            values = shlex.split(raw, comments=True)
        except ValueError:
            raise DeploymentError(f"{name} 的引号格式无效") from None
        if len(values) > 1:
            raise DeploymentError(f"{name} 包含未加引号的空格")
        if name in result:
            raise DeploymentError(f"环境文件重复定义 {name}")
        result[name] = values[0] if values else ""
    return result


def validate_environment(values: dict[str, str]) -> None:
    port = values.get("PLATFORM_PORT", DEFAULTS["PLATFORM_PORT"])
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise DeploymentError("PLATFORM_PORT 必须是 1 到 65535 的端口")
    project = values.get(
        "PLATFORM_COMPOSE_PROJECT", DEFAULTS["PLATFORM_COMPOSE_PROJECT"]
    )
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise DeploymentError(
            "PLATFORM_COMPOSE_PROJECT 只能使用小写字母、数字、下划线和短横线"
        )
    for name, value in values.items():
        if any(character in value for character in "\r\n\x00'"):
            raise DeploymentError(f"{name} 包含不支持的字符")


def initialize_environment(path: Path) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    temporary = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        values = read_environment(path)
        version = values.get("PLATFORM_DEPLOYMENT_VERSION")
        if version and version != DEFAULTS["PLATFORM_DEPLOYMENT_VERSION"]:
            raise DeploymentError("部署配置版本不受当前入口支持")
        if version:
            missing = [
                name
                for name in (*SECRET_KEYS, "LITELLM_MANAGEMENT_KEY")
                if not values.get(name)
            ]
            if missing:
                raise DeploymentError(
                    "已有部署缺少持久密钥 "
                    + ", ".join(missing)
                    + "，请从备份恢复环境文件"
                )
        if (
            values.get("RUOYI_DB_PASSWORD")
            and values.get("LITELLM_RUNTIME_KEY")
            and not values.get("PLATFORM_DEPLOYMENT_VERSION")
        ):
            raise DeploymentError(
                "检测到旧部署环境文件，请沿用 deploy/legacy/manage.sh；新部署请指定独立 PLATFORM_ENV_FILE 和数据目录"
            )
        validate_environment(values)
        for name, value in DEFAULTS.items():
            values.setdefault(name, value)
        for name in SECRET_KEYS:
            if not values.get(name):
                prefix = (
                    "sk-"
                    if name
                    in {"LITELLM_MASTER_KEY", "LITELLM_SALT_KEY", "LITELLM_RUNTIME_KEY"}
                    else ""
                )
                values[name] = prefix + secrets.token_hex(32)
        if not values.get("LITELLM_MANAGEMENT_KEY"):
            values["LITELLM_MANAGEMENT_KEY"] = values["LITELLM_MASTER_KEY"]
        validate_environment(values)
        output = "# 智衡统一部署配置；包含密钥，请勿提交 Git。\n" + "".join(
            f"{name}='{value}'\n" for name, value in values.items()
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        return values
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        os.close(descriptor)


def compose_command(path: Path, values: dict[str, str]) -> list[str]:
    return [
        os.environ.get("DOCKER_COMMAND", "docker"),
        "compose",
        "--project-directory",
        str(ROOT),
        "--project-name",
        values["PLATFORM_COMPOSE_PROJECT"],
        "--env-file",
        str(path),
        "-f",
        str(ROOT / "compose.yaml"),
    ]


def run(command: list[str], *, capture: bool = False) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
        return result.stdout or ""
    except FileNotFoundError:
        raise DeploymentError(
            "未找到 Docker，请先安装 Docker 和 Docker Compose v2"
        ) from None
    except subprocess.CalledProcessError:
        raise DeploymentError(
            "部署步骤执行失败，请查看上方错误；已有数据和密钥保留，可修正后重试"
        ) from None


def deploy(
    action: str, path: Path, *, build: bool = True, log_args: list[str] | None = None
) -> None:
    if action in {"logs", "status"}:
        _deploy(action, path, build=build, log_args=log_args)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(path.name + ".deploy.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeploymentError("另一个部署命令正在执行，请完成后再重试") from None
        _deploy(action, path, build=build, log_args=log_args)
    finally:
        os.close(descriptor)


def _deploy(
    action: str, path: Path, *, build: bool, log_args: list[str] | None
) -> None:
    docker = os.environ.get("DOCKER_COMMAND", "docker")
    run([docker, "compose", "version", "--short"], capture=True)
    if action == "init":
        values = initialize_environment(path)
    else:
        values = read_environment(path)
        if not values.get("PLATFORM_DEPLOYMENT_VERSION"):
            raise DeploymentError(
                "尚未初始化统一部署，请先执行 ./deploy.sh init；旧实例使用 deploy/legacy/manage.sh"
            )
        validate_environment(values)
    compose = compose_command(path, values)
    if action in {"init", "start", "update", "restart"}:
        if not (ROOT / "ruoyi-fastapi-frontend/dist/index.html").is_file():
            raise DeploymentError(
                "缺少前端 dist/index.html，请拉取包含构建产物的完整仓库"
            )
        data_dir = Path(values["PLATFORM_DATA_DIR"])
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        for name in ("agent-skills", "knowledge", "platform-files", "milvus"):
            (data_dir / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        run(compose + ["config", "--quiet"])
        if build and action in {"init", "update"}:
            run(compose + ["build", "ruoyi-backend-pg"])
        existing_app = action in {"init", "start"} and bool(
            run(compose + ["ps", "-q", "ruoyi-backend-pg"], capture=True).strip()
        )
        if action in {"update", "restart"} or existing_app:
            # 迁移时停应用写入，保留数据库与所有持久卷。
            run(compose + ["stop", "ruoyi-frontend", "ruoyi-backend-pg"])
        if action in {"init", "update"}:
            run(compose + ["rm", "-f", "platform-init"])
        arguments = ["up", "-d", "--wait", "--wait-timeout", "600"]
        arguments += ["--no-build"]
        run(compose + arguments)
        print(f"平台已就绪：http://127.0.0.1:{values['PLATFORM_PORT']}/", flush=True)
    elif action == "logs":
        run(compose + ["logs", "-f", *(log_args or [])])
    else:
        run(compose + [{"status": "ps"}.get(action, action)])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="智衡完整平台一键部署（PostgreSQL + Milvus）"
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="init",
        choices=(
            "init",
            "start",
            "restart",
            "stop",
            "status",
            "logs",
            "update",
            "help",
        ),
    )
    parser.add_argument("--no-build", action="store_true", help="使用已有后端镜像")
    args, remaining = parser.parse_known_args()
    if args.action == "help":
        parser.print_help()
        return 0
    if remaining and args.action != "logs":
        parser.error("无法识别的参数")
    path = Path(
        os.environ.get(
            "PLATFORM_ENV_FILE", os.environ.get("RUOYI_ENV_FILE", ROOT / ".env")
        )
    ).absolute()
    try:
        deploy(args.action, path, build=not args.no_build, log_args=remaining)
    except DeploymentError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
