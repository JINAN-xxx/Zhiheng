"""Executed inside a backend image; reports no credentials or business data."""

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen

REQUIRED_ROUTES = {
    "ai": ["/ai/runtime/health", "/ai/knowledge/databases"],
    "agent_extension": ["/agent_extension/skills", "/agent_extension/agents"],
}


def validate_snapshot(snapshot: dict, definitions: dict) -> None:
    rows = {row["plugin_id"]: row for row in snapshot["plugins"]}
    if not set(REQUIRED_ROUTES).issubset(rows):
        raise ValueError("内置插件安装记录不完整")
    expected = {}
    for plugin_id, row in rows.items():
        definition = definitions.get(plugin_id)
        if (
            not definition
            or row["status"] != "installed"
            or row["version"] != row["installed_version"]
        ):
            raise ValueError(f"插件 {plugin_id} 状态或版本不支持快捷发布")
        if row["installed_version"] != definition["version"] or row["enabled"] not in {
            "0",
            "1",
        }:
            raise ValueError(f"插件 {plugin_id} 与镜像版本不一致")
        expected.update(
            {
                (plugin_id, path): checksum
                for path, checksum in definition["migrations"].items()
            }
        )
    actual = {}
    for row in snapshot["migrations"]:
        if row["status"] != "success":
            raise ValueError("数据库存在未成功的迁移")
        actual[(row["plugin_id"], row["migration_path"])] = row["migration_checksum"]
    if actual != expected:
        raise ValueError("数据库迁移集合或校验值与发布镜像不一致")


def validate_recovery(before: dict, current: dict) -> None:
    def identities(rows):
        return [{k: v for k, v in row.items() if k != "status"} for row in rows]

    if (
        identities(before["plugins"]) != identities(current["plugins"])
        or before["migrations"] != current["migrations"]
    ):
        raise ValueError("数据库版本、启停状态或迁移记录已变化，拒绝自动恢复插件状态")
    if any(row["status"] != "installed" for row in before["plugins"]):
        raise ValueError("原插件状态不是健康基线")
    if any(row["status"] not in {"installed", "error"} for row in current["plugins"]):
        raise ValueError("当前插件状态不支持自动恢复")


def recovery_baseline(target: dict, current: dict) -> dict:
    """Keep operator enablement choices; versions and migrations must still match."""
    enabled = {row["plugin_id"]: row["enabled"] for row in current["plugins"]}
    if any(value not in {"0", "1"} for value in enabled.values()):
        raise ValueError("插件启停状态无效")
    expected = {
        "plugins": [
            dict(row, enabled=enabled.get(row["plugin_id"]))
            for row in target["plugins"]
        ],
        "migrations": target["migrations"],
    }
    validate_recovery(expected, current)
    return {
        "plugins": [dict(row, status="installed") for row in current["plugins"]],
        "migrations": current["migrations"],
    }


def validate_routes(paths: dict, snapshot: dict) -> None:
    required = ["/transport/crypto/frontend-config"]
    for row in snapshot["plugins"]:
        if row["enabled"] == "0":
            required.extend(REQUIRED_ROUTES.get(row["plugin_id"], []))
    if any("get" not in paths.get(path, {}) for path in required):
        raise ValueError("后端必需插件路由未就绪")


def database_snapshot(cursor) -> dict:
    cursor.execute(
        "SELECT plugin_id,version,installed_version,enabled,status FROM sys_plugin WHERE installed_version IS NOT NULL ORDER BY plugin_id"
    )
    plugins = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        "SELECT plugin_id,migration_path,migration_checksum,version,status FROM sys_plugin_migration ORDER BY plugin_id,migration_path"
    )
    migrations = [dict(row) for row in cursor.fetchall()]
    return {"plugins": plugins, "migrations": migrations}


def definitions() -> dict:
    import yaml

    result = {}
    for path in sorted(Path("/app/plugins").glob("*/plugin.yaml")):
        manifest = yaml.safe_load(path.read_text())
        migrations = manifest.get("backend", {}).get("migrations", [])
        result[manifest["id"]] = {
            "version": str(manifest["version"]),
            "migrations": {
                name: hashlib.sha256((path.parent / name).read_bytes()).hexdigest()
                for name in migrations
                if "/mysql/" not in name
            },
        }
    return result


def clear_ready_marker() -> None:
    import redis

    digest = hashlib.sha256(
        ("release:" + os.environ["APP_RELEASE_ID"]).encode()
    ).hexdigest()[:24]
    connection = redis.Redis(
        host=os.environ["REDIS_HOST"],
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ["REDIS_PASSWORD"],
        db=int(os.environ.get("REDIS_DATABASE", "2")),
        socket_timeout=10,
        socket_connect_timeout=10,
    )
    connection.delete("plugin:startup:ready:" + digest)
    connection.close()


def main() -> None:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    mode = sys.argv[1]
    if mode == "clear-marker":
        clear_ready_marker()
        return
    connection = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ["DB_DATABASE"],
        user=os.environ["DB_USERNAME"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
    )
    try:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            if mode == "restore":
                before = json.loads(sys.argv[2])
                cursor.execute("LOCK TABLE sys_plugin IN SHARE ROW EXCLUSIVE MODE")
                cursor.execute("LOCK TABLE sys_plugin_migration IN SHARE MODE")
                validate_recovery(before, database_snapshot(cursor))
                for row in before["plugins"]:
                    cursor.execute(
                        "UPDATE sys_plugin SET status='installed',last_error=NULL WHERE plugin_id=%s AND status='error'",
                        (row["plugin_id"],),
                    )
                return
            snapshot = database_snapshot(cursor)
            if mode != "snapshot-raw":
                validate_snapshot(snapshot, definitions())
            if mode == "health":
                with urlopen(
                    "http://127.0.0.1:9099/openapi.json", timeout=15
                ) as response:
                    validate_routes(json.load(response)["paths"], snapshot)
            print(json.dumps(snapshot, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- redact database driver errors at the process boundary
        # Driver exceptions may contain DSNs; print only our own validation text.
        print(
            str(error)
            if isinstance(error, ValueError)
            else "发布健康/状态检查执行失败",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
