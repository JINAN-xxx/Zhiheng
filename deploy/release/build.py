"""Build complete image bundles from a temporary, secret-free source snapshot."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .manifest import (
    PLATFORM,
    ROLES,
    ReleaseError,
    sha256_file,
    validate_version,
    write_manifest,
)

BACKEND = "backend"
FRONTEND = "frontend"
INFRA_ROLES = ("postgres", "redis", "litellm", "milvus")
IMAGE_VARIABLES = {
    "frontend": "PLATFORM_FRONTEND_IMAGE",
    "postgres": "LITELLM_POSTGRES_IMAGE",
    "redis": "REDIS_IMAGE",
    "litellm": "LITELLM_IMAGE",
    "milvus": "MILVUS_IMAGE",
}
EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "data",
    "vf_admin",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".cache",
    ".vite",
    ".idea",
    ".vscode",
    "htmlcov",
    "build",
    "coverage",
}
PUBLIC_FRONTEND_ENV = "VITE_APP_TITLE=智衡 AI\nVITE_APP_ENV=docker\nVITE_APP_BASE_API=/docker-api\nVITE_BUILD_COMPRESS=gzip\n"


def run(
    args: list[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    # Vite exposes inherited VITE_* values in the compiled browser application.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("VITE_")
    }
    try:
        return subprocess.run(
            args, cwd=cwd, check=check, text=True, capture_output=True, env=environment
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise ReleaseError(f"Command failed ({args[0]}): {detail[-4000:]}") from exc


def _excluded(path: Path) -> bool:
    name = path.name
    return (
        name in EXCLUDED_DIRECTORIES
        or name.startswith(".env")
        or name.endswith((".pem", ".key", ".pyc", ".pyo", ".log", ".egg-info"))
        or name in (".coverage", ".DS_Store")
    )


def _snapshot_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ReleaseError(f"Source must be a directory without symlinks: {source}")
    destination.mkdir(parents=True)
    for path in sorted(source.iterdir()):
        if _excluded(path):
            continue
        if path.is_symlink():
            raise ReleaseError(
                f"Source symlinks cannot enter a release snapshot: {path}"
            )
        target = destination / path.name
        if path.is_dir():
            _snapshot_tree(path, target)
        elif path.is_file():
            shutil.copy2(path, target)
        else:
            raise ReleaseError(f"Unsupported source file: {path}")


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ReleaseError(f"Required release file is missing or symlinked: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_runtime(root: Path, bundle: Path) -> None:
    instructions = root / "deploy/release/README.md"
    if instructions.is_file():
        _copy_file(instructions, bundle / "README.md")
    for source, target in (
        ("deploy/compose.release.yaml", "compose.release.yaml"),
        ("deploy/release.sh", "release.sh"),
        ("deploy/platform.py", "deploy/platform.py"),
        ("deploy/release_cli.py", "deploy/release_cli.py"),
        ("deploy/litellm/config.yaml", "deploy/litellm/config.yaml"),
        ("deploy/release/data-version", "deploy/release/data-version"),
    ):
        _copy_file(root / source, bundle / target)
    modules = sorted((root / "deploy/release").glob("*.py"))
    for name in ("__init__.py", "manifest.py", "environment.py", "manager.py"):
        if not (root / "deploy/release" / name).is_file():
            raise ReleaseError(f"Required release runtime module is missing: {name}")
    for path in modules:
        _copy_file(path, bundle / "deploy/release" / path.name)
    configurations = sorted((root / "deploy/milvus").glob("*.yaml"))
    if not configurations:
        raise ReleaseError("Missing Milvus runtime configuration")
    for path in configurations:
        _copy_file(path, bundle / "deploy/milvus" / path.name)
    (bundle / "release.sh").chmod(0o755)


def _hash_entries(entries: dict) -> str:
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _tree_hash(root: Path) -> str:
    return _hash_entries(
        {
            path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }
    )


def _data_hash(snapshot: Path, bundle: Path) -> str:
    backend = snapshot / BACKEND
    paths = set((backend / "sql").glob("*.sql"))
    paths.add(backend / "scripts/initialize_platform.py")
    for path in (backend / "plugins").rglob("*"):
        relative = path.relative_to(backend / "plugins")
        if path.is_file() and (
            path.name in ("plugin.yaml", "plugin.yml", "manifest.json")
            or {"migrations", "seeds"} & set(relative.parts)
        ):
            paths.add(path)
    entries = {
        path.relative_to(snapshot).as_posix(): sha256_file(path)
        for path in sorted(paths)
    }
    entries["deploy/release/data-version"] = sha256_file(
        bundle / "deploy/release/data-version"
    )
    return _hash_entries(entries)


def _runtime_hash(bundle: Path, images: dict) -> str:
    paths = [
        bundle / name
        for name in (
            "compose.release.yaml",
            "deploy/platform.py",
            "deploy/release/environment.py",
            "deploy/litellm/config.yaml",
        )
    ]
    paths.extend(sorted((bundle / "deploy/milvus").glob("*.yaml")))
    return _hash_entries(
        {
            "images": {role: images[role]["id"] for role in INFRA_ROLES},
            "files": {
                path.relative_to(bundle).as_posix(): sha256_file(path) for path in paths
            },
        }
    )


def _base_images(compose: Path) -> dict:
    content = compose.read_text(encoding="utf-8")
    images = {}
    for role, variable in IMAGE_VARIABLES.items():
        values = set(re.findall(r"\$\{" + variable + r":-([^}\s]+)\}", content))
        if len(values) != 1:
            raise ReleaseError(
                f"Compose must contain one unambiguous default for {variable}"
            )
        image = values.pop()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", image):
            raise ReleaseError(f"Invalid default image reference for {variable}")
        images[role] = image
    return images


def _inspect(reference: str, *, optional: bool = False) -> dict | None:
    result = run(["docker", "image", "inspect", reference], check=False)
    if result.returncode:
        detail = (result.stderr or "") + (result.stdout or "")
        if optional and ("No such image" in detail or "No such object" in detail):
            return None
        raise ReleaseError(f"Cannot inspect Docker image {reference}: {detail[-1000:]}")
    try:
        image = json.loads(result.stdout)[0]
        identifier = image["Id"]
        platform = f"{image['Os']}/{image['Architecture']}"
        if platform != PLATFORM:
            raise ReleaseError(
                f"Image {reference} has platform {platform}; required {PLATFORM}"
            )
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", identifier):
            raise ValueError("invalid image ID")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ReleaseError(f"Invalid Docker image metadata for {reference}") from exc
    return image


def _ensure_image(reference: str) -> dict:
    image = _inspect(reference, optional=True)
    if image is None:
        run(["docker", "pull", "--platform", PLATFORM, reference])
        image = _inspect(reference)
    return image


def _build_image(
    context: Path, dockerfile: Path, iidfile: Path, *, build_args: tuple[str, ...] = ()
) -> dict:
    command = [
        "docker",
        "build",
        "--platform",
        PLATFORM,
        "--pull=false",
        "--iidfile",
        str(iidfile),
        "-f",
        str(dockerfile),
    ]
    for value in build_args:
        command.extend(["--build-arg", value])
    command.append(str(context))
    run(command)
    return _inspect(iidfile.read_text(encoding="utf-8").strip())


def _build_applications(
    snapshot: Path, temporary: Path, dependencies: Path, frontend_base: str
) -> dict:
    frontend = snapshot / FRONTEND
    (frontend / "node_modules").symlink_to(
        dependencies.resolve(), target_is_directory=True
    )
    (frontend / ".env.docker").write_text(PUBLIC_FRONTEND_ENV, encoding="utf-8")
    print("构建前端静态资源（临时目录）…", flush=True)
    run(["npm", "run", "build:docker"], cwd=frontend)
    if not (frontend / "dist/index.html").is_file():
        raise ReleaseError("Frontend build did not produce dist/index.html")
    backend = snapshot / BACKEND
    _copy_file(frontend / "package.json", backend / ".release-frontend/package.json")
    _snapshot_tree(frontend / "plugins", backend / ".release-frontend/plugins")
    dockerfile = backend / "Dockerfile.release"
    dockerfile.write_text(
        (backend / "Dockerfile.pg").read_text(encoding="utf-8")
        + "\nCOPY .release-frontend /frontend\nRUN rm -rf /app/.release-frontend\n",
        encoding="utf-8",
    )
    print("构建后端发布镜像…", flush=True)
    backend_image = _build_image(backend, dockerfile, temporary / "backend.id")
    frontend_context = temporary / "frontend-image"
    frontend_context.mkdir()
    shutil.copytree(frontend / "dist", frontend_context / "dist")
    _copy_file(frontend / "bin/nginx.dockerpg.conf", frontend_context / "nginx.conf")
    print("构建前端发布镜像…", flush=True)
    frontend_image = _build_image(
        frontend_context,
        snapshot / "Dockerfile.frontend",
        temporary / "frontend.id",
        build_args=(f"FRONTEND_BASE_IMAGE={frontend_base}",),
    )
    return {"backend": backend_image, "frontend": frontend_image}


def build_release(root: Path, output: Path, version: str) -> dict:
    """Publish a new complete directory; never mutate source files or existing tags."""
    validate_version(version)
    root = Path(root).resolve()
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ReleaseError(f"Release output already exists: {output}")
    dependencies = root / FRONTEND / "node_modules"
    if not dependencies.is_dir():
        raise ReleaseError(
            "Frontend node_modules is missing; install frontend dependencies before building"
        )
    try:
        with tempfile.TemporaryDirectory(
            prefix="zhiheng-release-"
        ) as temporary_directory:
            temporary = Path(temporary_directory)
            snapshot = temporary / "source"
            bundle = snapshot / "bundle"
            bundle.mkdir(parents=True)
            _copy_runtime(root, bundle)
            _copy_file(root / "compose.yaml", snapshot / "compose.yaml")
            _copy_file(
                root / "deploy/release/Dockerfile.frontend",
                snapshot / "Dockerfile.frontend",
            )
            for directory in (BACKEND, FRONTEND):
                _snapshot_tree(root / directory, snapshot / directory)
            source_hash = _tree_hash(snapshot)
            data_hash = _data_hash(snapshot, bundle)
            revision = run(["git", "rev-parse", "HEAD"], cwd=root, check=False)
            dirty = run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=root,
                check=False,
            )
            source = {
                "revision": revision.stdout.strip()
                if revision.returncode == 0
                else "unknown",
                "dirty": bool(dirty.returncode or dirty.stdout.strip()),
                "sha256": source_hash,
            }
            defaults = _base_images(snapshot / "compose.yaml")
            print("检查基础镜像与运行平台…", flush=True)
            bases = {
                role: _ensure_image(reference) for role, reference in defaults.items()
            }
            images = {role: bases[role] for role in INFRA_ROLES}
            images.update(
                _build_applications(
                    snapshot, temporary, dependencies, defaults["frontend"]
                )
            )
            manifest_images = {
                role: {
                    "tag": f"zhiheng-release/{role}:"
                    + (
                        f"{version}-{source_hash[:12]}"
                        if role in ROLES[:2]
                        else f"sha-{images[role]['Id'][7:19]}"
                    ),
                    "id": images[role]["Id"],
                }
                for role in ROLES
            }
            for item in manifest_images.values():
                existing = _inspect(item["tag"], optional=True)
                if existing and existing["Id"] != item["id"]:
                    raise ReleaseError(
                        f"Release image tag already exists with a different image: {item['tag']}"
                    )
            for item in manifest_images.values():
                run(["docker", "tag", item["id"], item["tag"]])
            print("导出六个镜像并校验发布文件…", flush=True)
            run(
                [
                    "docker",
                    "image",
                    "save",
                    "--output",
                    str(bundle / "images.tar"),
                    *[item["tag"] for item in manifest_images.values()],
                ]
            )
            manifest = write_manifest(
                bundle,
                {
                    "format": 1,
                    "version": version,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source": source,
                    "platform": PLATFORM,
                    "compatibility": {
                        "data_sha256": data_hash,
                        "runtime_sha256": _runtime_hash(bundle, manifest_images),
                    },
                    "images": manifest_images,
                },
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.mkdir()  # Exclusive creation also protects against another publisher.
            try:
                shutil.copytree(bundle, output, dirs_exist_ok=True)
            except BaseException:
                shutil.rmtree(output)
                raise
            return manifest
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"Release build failed: {exc}") from exc
