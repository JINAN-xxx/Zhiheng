"""Validate release metadata before using any paths or image references."""

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

ROLES = ("backend", "frontend", "postgres", "redis", "litellm", "milvus")
PLATFORM = "linux/amd64"
_HASH = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_FIRST_PRINTABLE = 32
_MAX_REVISION_LENGTH = 128


class ReleaseError(Exception):
    """An invalid release or a failed release operation."""


def validate_version(version: str) -> str:
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ReleaseError(
            "Invalid release version: use 1–64 ASCII letters, digits, dots, underscores, or hyphens"
        )
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseError(f"Cannot read release file: {path}") from exc
    return digest.hexdigest()


def _keys(value: object, expected: tuple, label: str) -> None:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ReleaseError(f"Invalid {label} fields")


def _hash(value: object, label: str) -> None:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ReleaseError(f"Invalid SHA256 for {label}")


def _file_path(bundle: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or any(ord(c) < _FIRST_PRINTABLE for c in relative)
    ):
        raise ReleaseError("Invalid release file path")
    parts = relative.split("/")
    if (
        PurePosixPath(relative).is_absolute()
        or any(part in ("", ".", "..") for part in parts)
        or "\\" in relative
        or ":" in relative
        or relative == "release.json"
    ):
        raise ReleaseError(f"Unsafe release file path: {relative!r}")
    path = bundle
    for part in parts:
        path /= part
        if path.is_symlink():
            raise ReleaseError(f"Symlink is not allowed in release files: {relative}")
    return path


def _validate_images(value: dict, version: str, source_hash: str) -> None:
    _keys(value, ROLES, "images")
    for role, image in value.items():
        _keys(image, ("tag", "id"), f"{role} image")
        if not isinstance(image["id"], str) or not image["id"].startswith("sha256:"):
            raise ReleaseError(f"Invalid image ID for {role}")
        _hash(image["id"][7:], f"{role} image ID")
        suffix = (
            f"{version}-{source_hash[:12]}"
            if role in ROLES[:2]
            else f"sha-{image['id'][7:19]}"
        )
        if image["tag"] != f"zhiheng-release/{role}:{suffix}":
            raise ReleaseError(f"Invalid release image tag for {role}")


def _validate(bundle: Path, value: dict, *, verify_files: bool) -> None:
    _keys(
        value,
        (
            "format",
            "version",
            "created_at",
            "source",
            "platform",
            "compatibility",
            "images",
            "files",
        ),
        "manifest",
    )
    if type(value["format"]) is not int or value["format"] != 1:
        raise ReleaseError("Unsupported release format")
    version = validate_version(value["version"])
    if value["platform"] != PLATFORM:
        raise ReleaseError(f"Unsupported release platform; expected {PLATFORM}")
    try:
        created = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("timezone required")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReleaseError("Invalid created_at timestamp") from exc
    source = value["source"]
    _keys(source, ("revision", "dirty", "sha256"), "source")
    revision = source["revision"]
    if (
        not isinstance(revision, str)
        or not revision
        or len(revision) > _MAX_REVISION_LENGTH
        or any(ord(c) < _FIRST_PRINTABLE for c in revision)
    ):
        raise ReleaseError("Invalid source revision")
    if type(source["dirty"]) is not bool:
        raise ReleaseError("Invalid source dirty flag")
    _hash(source["sha256"], "source")
    _keys(value["compatibility"], ("data_sha256", "runtime_sha256"), "compatibility")
    for name, digest in value["compatibility"].items():
        _hash(digest, name)
    _validate_images(value["images"], version, source["sha256"])
    if not isinstance(value["files"], dict) or not value["files"]:
        raise ReleaseError("Release file manifest is empty or invalid")
    for relative, digest in value["files"].items():
        path = _file_path(bundle, relative)
        _hash(digest, relative)
        if verify_files:
            if not path.is_file():
                raise ReleaseError(f"Release file is missing: {relative}")
            if sha256_file(path) != digest:
                raise ReleaseError(f"Release file SHA256 mismatch: {relative}")


def _bundle_path(bundle: Path) -> Path:
    bundle = Path(bundle).absolute()
    if bundle.is_symlink() or not bundle.is_dir():
        raise ReleaseError("Release bundle must be a directory, without symlinks")
    if (bundle / "release.json").is_symlink():
        raise ReleaseError("Release manifest cannot be a symlink")
    return bundle


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseError(f"Duplicate manifest key: {key}")
        result[key] = value
    return result


def load_manifest(bundle: Path, *, verify_files: bool = True) -> dict:
    bundle = _bundle_path(bundle)
    try:
        value = json.loads(
            (bundle / "release.json").read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReleaseError("Cannot read release.json") from exc
    _validate(bundle, value, verify_files=verify_files)
    return value


def write_manifest(bundle: Path, metadata: dict) -> dict:
    bundle = _bundle_path(bundle)
    value = dict(metadata)
    files = {}
    for directory, directories, filenames in os.walk(bundle, followlinks=False):
        for name in directories + filenames:
            path = Path(directory) / name
            if path.is_symlink():
                raise ReleaseError(
                    f"Symlink is not allowed in release bundle: {path.relative_to(bundle)}"
                )
        for name in filenames:
            path = Path(directory) / name
            relative = path.relative_to(bundle).as_posix()
            if relative != "release.json":
                if not path.is_file():
                    raise ReleaseError(
                        f"Release entry must be a regular file: {relative}"
                    )
                _file_path(bundle, relative)
                files[relative] = sha256_file(path)
    value["files"] = dict(sorted(files.items()))
    _validate(bundle, value, verify_files=False)
    try:
        (bundle / "release.json").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise ReleaseError("Cannot write release.json") from exc
    return value
