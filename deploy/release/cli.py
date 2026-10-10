"""Versioned release commands; no runtime dependency beyond Python's stdlib."""

import argparse
import sys
from pathlib import Path

from .environment import deployment_helpers
from .manager import ReleaseManager
from .manifest import ReleaseError, load_manifest


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="智衡镜像发布包：构建、校验、部署和应用回滚"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("build", help="从当前源码隔离构建离线发布目录")
    build.add_argument("--version", required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="校验发布清单与文件完整性")
    verify.add_argument("--bundle", type=Path, default=root)
    deploy = commands.add_parser(
        "deploy", help="部署当前发布包，兼容升级失败时恢复上一版本"
    )
    deploy.add_argument("--bundle", type=Path, default=root)
    deploy.add_argument(
        "--site", type=Path, required=True, help="固定安装目录，配置和数据保存在这里"
    )
    rollback = commands.add_parser("rollback", help="回滚应用版本，保留数据库和文件")
    rollback.add_argument("--site", type=Path, required=True)
    status = commands.add_parser("status", help="查看本地发布记录与未完成操作")
    status.add_argument("--site", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "build":
            from .build import build_release

            result = build_release(root, args.output.absolute(), args.version)
            print(f"发布包构建完成：{args.output.absolute()}，版本 {result['version']}")
        elif args.action == "verify":
            result = load_manifest(args.bundle)
            print(f"发布包内容校验通过：{result['version']}，{result['platform']}")
        else:
            manager = ReleaseManager(args.site)
            if args.action == "deploy":
                result = manager.deploy(args.bundle)
                print(
                    f"发布成功：{result['current']['version']}；安装目录 {manager.site}"
                )
            elif args.action == "rollback":
                result = manager.rollback()
                print(
                    f"应用已恢复至：{result['current']['version']}；数据库与持久文件保留"
                )
            else:
                result = manager.status()
                for label, key in (
                    ("最近成功版本", "current"),
                    ("上一成功版本", "previous"),
                ):
                    print(
                        f"{label}：{result[key]['version'] if result[key] else '未部署'}"
                    )
                pending = result["pending"]
                print(
                    f"未完成操作：{pending['kind'] + ' → ' + pending['to']['version'] if pending else '无'}"
                )
        return 0
    except (
        ReleaseError,
        ValueError,
        OSError,
        deployment_helpers().DeploymentError,
    ) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
