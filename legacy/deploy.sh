#!/bin/sh
set -eu
script_directory=$(CDPATH= cd "$(dirname "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '错误：一键部署需要 Python 3，请先安装 python3。' >&2
    exit 1
fi
exec python3 "$script_directory/deploy/platform.py" "$@"
