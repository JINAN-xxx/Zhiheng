"""Atomic release records around deliberately non-atomic container operations."""

import fcntl
import json
import os
import re
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .environment import (
    atomic_write,
    initialize_site,
    persistent_identity,
    read_site_environment,
)
from .manifest import ReleaseError, load_manifest, sha256_file, validate_version
from .probe import recovery_baseline, validate_recovery
from .runtime import DockerRuntime


class ReleaseManager:
    def __init__(self, site: Path, *, runtime=None):
        self.site = Path(site).absolute()
        if self.site.is_symlink():
            raise ReleaseError("安装目录不能是符号链接")
        self.runtime = runtime or DockerRuntime(self.site)

    @contextmanager
    def lock(self):
        self.site.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            self.site / ".release.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReleaseError("另一个发布操作正在执行，请稍后重试") from None
            yield
        finally:
            os.close(descriptor)

    def status(self) -> dict:
        path = self.site / "state.json"
        if path.is_symlink():
            raise ReleaseError("发布状态不能是符号链接")
        if not path.exists():
            values = read_site_environment(self.site)
            data = Path(values.get("PLATFORM_DATA_DIR", str(self.site / "data")))
            if not data.is_absolute():
                data = self.site / data
            if (
                values.get("PLATFORM_DEPLOYMENT_VERSION")
                or values.get("ZHIHENG_RELEASE_ENV_VERSION")
                or (self.site / "releases").exists()
                or (data.is_dir() and any(data.iterdir()))
            ):
                raise ReleaseError(
                    "已有实例的发布状态 state.json 缺失，拒绝作为新实例初始化；请恢复发布记录"
                )
            return {
                "format": 1,
                "site_id": None,
                "identity": None,
                "current": None,
                "previous": None,
                "pending": None,
            }
        try:
            state = json.loads(path.read_text())
            expected = {
                "format",
                "site_id",
                "identity",
                "current",
                "previous",
                "pending",
            }
            if not isinstance(state, dict) or set(state) != expected:
                raise ValueError("state fields")
            if type(state["format"]) is not int or state["format"] != 1:
                raise ValueError("state format")
            if not isinstance(state["site_id"], str) or not re.fullmatch(
                r"[0-9a-f]{32}", state["site_id"]
            ):
                raise ValueError("site identity")
            initialized = any(
                state[key] is not None for key in ("current", "previous", "pending")
            )
            if (state["identity"] is not None or initialized) and (
                not isinstance(state["identity"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", state["identity"])
            ):
                raise ValueError("persistent identity")
            for name in ("current", "previous"):
                if state[name] is not None:
                    validate_version(state[name]["version"])
                    if not re.fullmatch(
                        r"[0-9a-f]{64}", state[name]["manifest_sha256"]
                    ):
                        raise ValueError("record checksum")
                    if not isinstance(state[name]["snapshot"], dict):
                        raise ValueError("record snapshot")
            if state["previous"] is not None and state["current"] is None:
                raise ValueError("previous without current")
            pending = state["pending"]
            if pending is not None:
                if (
                    pending["kind"] not in ("deploy", "rollback")
                    or pending["from"] != state["current"]
                ):
                    raise ValueError("pending transition")
                validate_version(pending["to"]["version"])
                if not re.fullmatch(r"[0-9a-f]{64}", pending["to"]["manifest_sha256"]):
                    raise ValueError("pending checksum")
                if state["current"] is not None and not isinstance(
                    pending["snapshot"], dict
                ):
                    raise ValueError("pending snapshot")
            return state
        except (KeyError, TypeError, ValueError, ReleaseError):
            raise ReleaseError("发布状态损坏，请从备份恢复 state.json") from None

    def save(self, state: dict) -> None:
        atomic_write(
            self.site / "state.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )

    def prepare_configuration(self, bundle: Path) -> None:
        # Stable bind paths keep unchanged LiteLLM/Milvus containers intact when
        # the release directory changes. Never silently replace modified config.
        configuration = self.site / "runtime"
        if configuration.is_symlink():
            raise ReleaseError("运行配置目录不能是符号链接")
        for name in ("litellm", "milvus"):
            destination = configuration / name
            if destination.is_symlink():
                raise ReleaseError("运行配置目录不能是符号链接")
            for source in sorted((bundle / "deploy" / name).glob("*.yaml")):
                target = destination / source.name
                if target.is_symlink():
                    raise ReleaseError("运行配置文件不能是符号链接")
                if target.exists():
                    if sha256_file(target) != sha256_file(source):
                        raise ReleaseError("实例运行配置与发布包不一致，拒绝覆盖")
                else:
                    atomic_write(target, source.read_text())

    def stored(self, record: dict) -> tuple[Path, dict]:
        path = self.site / "releases" / validate_version(record["version"])
        manifest = load_manifest(path)
        if sha256_file(path / "release.json") != record["manifest_sha256"]:
            raise ReleaseError("已记录的发布版本清单发生变化")
        return path, manifest

    def stage(self, bundle: Path, manifest: dict) -> tuple[Path, dict]:
        record = {
            "version": manifest["version"],
            "manifest_sha256": sha256_file(bundle / "release.json"),
        }
        releases = self.site / "releases"
        if releases.is_symlink():
            raise ReleaseError("版本目录不能是符号链接")
        releases.mkdir(mode=0o700, exist_ok=True)
        target = releases / manifest["version"]
        if target.exists() or target.is_symlink():
            self.stored(record)
            return target, record
        temporary = Path(tempfile.mkdtemp(prefix=".stage-", dir=releases))
        try:
            for relative in (*manifest["files"], "release.json"):
                destination = temporary / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(bundle / relative, destination)
                os.chmod(destination, 0o700 if relative.endswith(".sh") else 0o600)
            load_manifest(temporary)
            if sha256_file(temporary / "release.json") != record["manifest_sha256"]:
                raise ReleaseError("发布包在复制期间发生变化")
            temporary.rename(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return target, record

    @staticmethod
    def compatible(first: dict, second: dict) -> None:
        if (
            first["compatibility"] != second["compatibility"]
            or first["platform"] != second["platform"]
        ):
            raise ReleaseError(
                "数据或基础运行契约不兼容；快捷更新/回滚已拒绝，请先制定备份与迁移方案"
            )

    def existing_environment(self, state: dict) -> dict | None:
        if state["identity"]:
            if not (self.site / ".env").is_file():
                raise ReleaseError(
                    "已有实例配置 .env 丢失，请从备份恢复，不能重新生成密钥"
                )
            values = read_site_environment(self.site)
            if persistent_identity(values) != state["identity"]:
                raise ReleaseError(
                    "实例持久配置与发布记录不一致，请恢复原数据库、数据路径及密钥配置"
                )
            return values
        return None

    def _restore(
        self, target: dict, snapshot: dict, values: dict, site_id: str
    ) -> None:
        path, manifest = self.stored(target)
        self.prepare_configuration(path)
        self.runtime.load_images(path, manifest)
        self.runtime.configure(path, manifest, values, site_id)
        self.runtime.check_ownership()
        self.runtime.quiesce_operations()
        self.runtime.validate()
        self.runtime.stop_applications()
        self.runtime.restore_plugin_state(snapshot)
        self.runtime.start_applications()
        self.runtime.verify()

    def deploy(self, bundle: Path) -> dict:
        bundle = Path(bundle).absolute()
        with self.lock():
            manifest = load_manifest(bundle)
            if not {"images.tar", "compose.release.yaml"}.issubset(manifest["files"]):
                raise ReleaseError("发布包缺少镜像或运行编排")
            state = self.status()
            values = self.existing_environment(state)
            pending = state["pending"]
            if pending and (
                state["current"] or pending["to"]["version"] != manifest["version"]
            ):
                raise ReleaseError(
                    "存在未完成发布，请先执行 rollback；首次失败只允许重试原版本"
                )
            if state["current"]:
                _, current_manifest = self.stored(state["current"])
                self.compatible(current_manifest, manifest)
            if not state["site_id"]:
                state["site_id"] = secrets.token_hex(16)
                self.save(state)
            target, record = self.stage(bundle, manifest)
            self.prepare_configuration(target)
            self.runtime.load_images(target, manifest)
            if values is None:
                values = initialize_site(
                    self.site, manifest["images"]["backend"]["tag"], self.runtime.run
                )
            site_id = state["site_id"] or secrets.token_hex(16)
            self.runtime.configure(target, manifest, values, site_id)
            self.runtime.check_ownership()
            if pending:
                self.runtime.quiesce_operations()
            self.runtime.validate()
            snapshot = None
            if state["current"]:
                # Query through the CURRENT image before allowing initialization
                # to repair missing migrations or overwrite plugin versions.
                current_path, current_manifest = self.stored(state["current"])
                self.runtime.configure(current_path, current_manifest, values, site_id)
                self.runtime.verify()
                snapshot = self.runtime.snapshot_plugins()
                state["current"]["snapshot"] = snapshot
                self.runtime.configure(target, manifest, values, site_id)
            state.update(site_id=site_id, identity=persistent_identity(values))
            state["pending"] = {
                "kind": "deploy",
                "from": state["current"],
                "to": record,
                "snapshot": snapshot,
            }
            self.save(state)
            try:
                self.runtime.start_infrastructure()
                self.runtime.stop_applications()
                self.runtime.initialize()
                self.runtime.start_applications()
                self.runtime.verify()
                after = self.runtime.snapshot_plugins()
                if snapshot is not None:
                    validate_recovery(snapshot, after)
                record["snapshot"] = after
            except Exception as error:
                if state["current"]:
                    try:
                        self._restore(state["current"], snapshot, values, site_id)
                    except Exception as recovery_error:
                        raise ReleaseError(
                            "新版本发布失败且恢复失败；保留未完成记录，请检查容器日志后执行 rollback"
                        ) from recovery_error
                    state["pending"] = None
                    self.save(state)
                    raise ReleaseError(
                        "新版本发布失败，已恢复上一运行版本；本次发布未成功"
                    ) from error
                raise ReleaseError(
                    "首次部署失败，配置和数据已保留；修正问题后重试同一发布包"
                ) from error
            if not state["current"] or state["current"]["version"] != record["version"]:
                state["previous"] = state["current"]
            state.update(current=record, pending=None)
            self.save(state)
            return state

    def rollback(self) -> dict:
        with self.lock():
            state = self.status()
            values = self.existing_environment(state)
            pending = state["pending"]
            target = (
                pending["to" if pending["kind"] == "rollback" else "from"]
                if pending
                else state["previous"]
            )
            if not target or values is None:
                raise ReleaseError("没有可回滚的成功版本；首次部署失败请重试同一发布包")
            current_path, current_manifest = self.stored(state["current"])
            _, target_manifest = self.stored(target)
            self.compatible(current_manifest, target_manifest)
            snapshot = pending["snapshot"] if pending else target["snapshot"]
            if pending and pending["kind"] == "rollback":
                snapshot = recovery_baseline(target["snapshot"], snapshot)
            if not pending:
                self.runtime.configure(
                    current_path, current_manifest, values, state["site_id"]
                )
                self.runtime.check_ownership()
                current_snapshot = self.runtime.recovery_snapshot()
                snapshot = recovery_baseline(target["snapshot"], current_snapshot)
                state["pending"] = {
                    "kind": "rollback",
                    "from": state["current"],
                    "to": target,
                    "snapshot": snapshot,
                }
                self.save(state)
            try:
                self._restore(target, snapshot, values, state["site_id"])
            except Exception as error:
                raise ReleaseError(
                    "应用恢复失败，未完成记录已保留；修正问题后重试 rollback"
                ) from error
            if not pending or pending["kind"] == "rollback":
                state["previous"] = state["current"]
            target["snapshot"] = snapshot
            state.update(current=target, pending=None)
            self.save(state)
            return state
