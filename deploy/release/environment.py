"""Private, installation-scoped configuration for image releases."""

import hashlib
import importlib.util
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

IMAGE_ROLES = ("backend", "frontend", "postgres", "redis", "litellm", "milvus")
PROCESS_ENVIRONMENT = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "XDG_RUNTIME_DIR",
    "SSH_AUTH_SOCK",
)
CRYPTO_SCRIPT = """import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
print(json.dumps({
    "private": k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode(),
    "public": k.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo).decode()
}))
"""


@lru_cache(maxsize=1)
def deployment_helpers():
    # Avoid importing the standard library's module named "platform".
    spec = importlib.util.spec_from_file_location(
        "zhiheng_deployment_environment", Path(__file__).parents[1] / "platform.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic_write(path: Path, content: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"{path.name} 必须是普通文件")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_environment(path: Path, values: dict[str, str]) -> None:
    deployment_helpers().validate_environment(values)
    atomic_write(
        path,
        "# 智衡发布实例配置；含持久密钥，请备份且勿随发布包分发。\n"
        + "".join(f"{name}='{value}'\n" for name, value in values.items()),
    )


def read_site_environment(site: Path) -> dict[str, str]:
    values = deployment_helpers().read_environment(site / ".env")
    deployment_helpers().validate_environment(values)
    return values


def initialize_site(site: Path, backend_image: str, run) -> dict[str, str]:
    """Create one identity, then only reuse it; missing saved keys are an error."""
    site = site.absolute()
    if site.is_symlink():
        raise ValueError("安装目录不能是符号链接")
    site.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = site / ".env"
    original = read_site_environment(site)
    private = original.get("TRANSPORT_CRYPTO_PRIVATE_KEY")
    public = original.get("TRANSPORT_CRYPTO_PUBLIC_KEY")
    if bool(private) != bool(public) or (
        original.get("ZHIHENG_RELEASE_ENV_VERSION") and not private
    ):
        raise ValueError("已有实例传输密钥不完整，请从备份恢复密钥，不会重新生成")
    if not original.get("PLATFORM_DEPLOYMENT_VERSION"):
        defaults = dict(original)
        defaults.setdefault(
            "PLATFORM_COMPOSE_PROJECT",
            "zhiheng-" + hashlib.sha256(str(site).encode()).hexdigest()[:12],
        )
        defaults.setdefault("PLATFORM_DATA_DIR", str(site / "data"))
        write_environment(path, defaults)
    values = deployment_helpers().initialize_environment(path)
    data = Path(values["PLATFORM_DATA_DIR"])
    if not data.is_absolute():
        data = site / data
    values["PLATFORM_DATA_DIR"] = str(data.resolve())
    if not private:
        generated = json.loads(
            run(
                [
                    os.environ.get("DOCKER_COMMAND", "docker"),
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    backend_image,
                    "-c",
                    CRYPTO_SCRIPT,
                ],
                capture=True,
            )
        )
        if not all(
            isinstance(generated.get(key), str) and generated[key]
            for key in ("private", "public")
        ):
            raise ValueError("传输密钥生成失败")
        values["TRANSPORT_CRYPTO_PRIVATE_KEY"] = generated["private"].replace(
            "\n", "\\n"
        )
        values["TRANSPORT_CRYPTO_PUBLIC_KEY"] = generated["public"].replace("\n", "\\n")
    values.setdefault(
        "TRANSPORT_CRYPTO_KID",
        "release-" + hashlib.sha256(str(site).encode()).hexdigest()[:12],
    )
    values.setdefault("MILVUS_MEMORY_LIMIT", "4g")
    values["ZHIHENG_RELEASE_ENV_VERSION"] = "1"
    write_environment(path, values)
    for name in ("agent-skills", "knowledge", "platform-files", "milvus"):
        (data / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    return values


def process_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in PROCESS_ENVIRONMENT if key in os.environ}


def compose_environment(
    values: dict[str, str], manifest: dict, site_id: str
) -> dict[str, str]:
    result = process_environment()
    result.update(
        {
            key: value
            for key, value in values.items()
            if not key.startswith(("COMPOSE_", "DOCKER_", "RELEASE_"))
        }
    )
    for role in IMAGE_ROLES:
        result[f"RELEASE_{role.upper()}_IMAGE"] = manifest["images"][role]["tag"]
    result["RELEASE_VERSION"] = manifest["version"]
    result["RELEASE_SITE_ID"] = site_id
    return result


def persistent_identity(values: dict[str, str]) -> str:
    keys = (
        "PLATFORM_COMPOSE_PROJECT",
        "PLATFORM_DATA_DIR",
        "PLATFORM_DB_NAME",
        "PLATFORM_DB_USER",
        *deployment_helpers().SECRET_KEYS,
        "LITELLM_MANAGEMENT_KEY",
        "TRANSPORT_CRYPTO_PRIVATE_KEY",
        "TRANSPORT_CRYPTO_PUBLIC_KEY",
        "TRANSPORT_CRYPTO_KID",
    )
    payload = {key: values.get(key) for key in keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
