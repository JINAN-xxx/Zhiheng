"""Docker adapter for release operations, with an explicit process environment."""

import json
import os
import subprocess
from pathlib import Path

from .environment import compose_environment, process_environment
from .manifest import ReleaseError
from .probe import validate_routes

INFRASTRUCTURE = (
    "litellm-db",
    "litellm-redis",
    "zhiheng-redis",
    "litellm-proxy",
    "milvus",
)
APPLICATIONS = ("zhiheng-frontend", "zhiheng-backend-pg")


class DockerRuntime:
    def __init__(self, site: Path):
        self.site = site
        self.docker = os.environ.get("DOCKER_COMMAND", "docker")
        self.environment = process_environment()

    def run(self, command: list[str], *, capture: bool = False) -> str:
        try:
            result = subprocess.run(
                command,
                check=True,
                text=True,
                env=self.environment,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
            )
            return result.stdout or ""
        except FileNotFoundError:
            raise ReleaseError("未找到 Docker，请安装 Docker 与 Compose v2") from None
        except subprocess.CalledProcessError:
            raise ReleaseError(
                "Docker 发布步骤执行失败；可检查实例容器日志，配置和数据保留"
            ) from None

    def load_images(self, bundle: Path, manifest: dict) -> None:
        for image in manifest["images"].values():
            existing = self.run(
                [self.docker, "image", "ls", "--quiet", "--no-trunc", image["tag"]],
                capture=True,
            ).split()
            if existing and set(existing) != {image["id"]}:
                raise ReleaseError(
                    "已有镜像标签指向其他内容，拒绝覆盖；请核对发布包版本"
                )
        self.run([self.docker, "load", "--input", str(bundle / "images.tar")])
        for role, image in manifest["images"].items():
            result = json.loads(
                self.run(
                    [
                        self.docker,
                        "image",
                        "inspect",
                        "--format",
                        "{{json .}}",
                        image["tag"],
                    ],
                    capture=True,
                )
            )
            if (
                result["Id"] != image["id"]
                or f"{result['Os']}/{result['Architecture']}" != manifest["platform"]
            ):
                raise ReleaseError(f"{role} 镜像与发布清单不匹配")

    def configure(
        self, bundle: Path, manifest: dict, values: dict, site_id: str
    ) -> None:
        self.bundle, self.manifest, self.values, self.site_id = (
            bundle,
            manifest,
            values,
            site_id,
        )
        self.environment = compose_environment(values, manifest, site_id)
        self.environment["RELEASE_CONFIG_DIR"] = str(self.site / "runtime")
        self.compose = [
            self.docker,
            "compose",
            "--project-directory",
            str(bundle),
            "--project-name",
            values["PLATFORM_COMPOSE_PROJECT"],
            "--env-file",
            str(self.site / ".env"),
            "-f",
            str(bundle / "compose.release.yaml"),
        ]

    def containers(self) -> list[dict]:
        ids = self.run(
            [
                self.docker,
                "ps",
                "--all",
                "--filter",
                f"label=com.docker.compose.project={self.values['PLATFORM_COMPOSE_PROJECT']}",
                "--format",
                "{{.ID}}",
            ],
            capture=True,
        ).split()
        return (
            json.loads(self.run([self.docker, "inspect", *ids], capture=True))
            if ids
            else []
        )

    def check_ownership(self) -> None:
        for container in self.containers():
            if (
                container["Config"]["Labels"].get("io.zhiheng.installation")
                != self.site_id
            ):
                raise ReleaseError(
                    "同 Compose 项目存在其他实例或源码部署的容器，拒绝接管"
                )

    def validate(self) -> None:
        self.run(self.compose + ["config", "--quiet"])

    def quiesce_operations(self) -> None:
        # A killed manager can leave docker compose run executing in the daemon.
        # Stop only this installation's one-off writers before recovery/retry.
        orphans = []
        for container in self.containers():
            labels = container["Config"]["Labels"]
            if (
                labels.get("io.zhiheng.installation") == self.site_id
                and labels.get("com.docker.compose.oneoff", "").lower() == "true"
                and labels.get("com.docker.compose.service")
                in ("platform-init", "zhiheng-backend-pg")
                and container["State"]["Status"] == "running"
            ):
                orphans.append(container["Id"])
        if orphans:
            self.run([self.docker, "stop", "--time", "30", *orphans])

    def start_infrastructure(self) -> None:
        self.run(
            self.compose
            + [
                "up",
                "-d",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "600",
                *INFRASTRUCTURE,
            ]
        )

    def stop_applications(self) -> None:
        self.run(self.compose + ["stop", *APPLICATIONS])

    def _probe(
        self, mode: str, snapshot: dict | None = None, *, running: bool = False
    ) -> dict | None:
        code = Path(__file__).with_name("probe.py").read_text()
        command = (
            ["exec", "-T", "zhiheng-backend-pg"]
            if running
            else [
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "python",
                "zhiheng-backend-pg",
            ]
        )
        if running:
            command += ["python"]
        command += ["-c", code, mode]
        if snapshot is not None:
            command.append(json.dumps(snapshot, sort_keys=True))
        output = self.run(self.compose + command, capture=True)
        return json.loads(output) if output.strip() else None

    def snapshot_plugins(self) -> dict:
        return self._probe("snapshot")

    def recovery_snapshot(self) -> dict:
        return self._probe("snapshot-raw")

    def restore_plugin_state(self, snapshot: dict) -> None:
        self._probe("restore", snapshot)

    def initialize(self) -> None:
        self.run(self.compose + ["run", "--rm", "--no-deps", "-T", "platform-init"])

    def start_applications(self) -> None:
        # All application writers have been stopped. A rollback must re-run the
        # target version's normal menu/job synchronization, not reuse its old marker.
        self._probe("clear-marker")
        for service in ("zhiheng-backend-pg", "zhiheng-frontend"):
            self.run(
                self.compose
                + [
                    "up",
                    "-d",
                    "--no-deps",
                    "--no-build",
                    "--wait",
                    "--wait-timeout",
                    "600",
                    service,
                ]
            )

    def verify(self) -> None:
        snapshot = self._probe("health", running=True)
        for route in ("/", "/docker-api/transport/crypto/frontend-config"):
            self.run(
                self.compose
                + [
                    "exec",
                    "-T",
                    "zhiheng-frontend",
                    "curl",
                    "--fail",
                    "--silent",
                    "--output",
                    "/dev/null",
                    "http://127.0.0.1" + route,
                ],
                capture=True,
            )
        response = self.run(
            self.compose
            + [
                "exec",
                "-T",
                "zhiheng-frontend",
                "curl",
                "--fail",
                "--silent",
                "http://127.0.0.1/docker-api/openapi.json",
            ],
            capture=True,
        )
        try:
            schema = json.loads(response)
            validate_routes(schema["paths"], snapshot)
            if schema["info"]["version"] != self.manifest["version"]:
                raise ValueError("version mismatch")
        except (ValueError, KeyError, TypeError):
            raise ReleaseError("前端反代未返回当前版本的完整后端接口") from None
        containers = self.containers()
        for service, role in (
            ("zhiheng-backend-pg", "backend"),
            ("zhiheng-frontend", "frontend"),
        ):
            active = [
                item
                for item in containers
                if item["Config"]["Labels"].get("com.docker.compose.service") == service
                and item["State"]["Status"] == "running"
            ]
            if (
                len(active) != 1
                or active[0]["Image"] != self.manifest["images"][role]["id"]
            ):
                raise ReleaseError("实际运行的前后端镜像与发布版本不一致")
