#!/bin/sh
set -eu
release_directory=$(CDPATH= cd "$(dirname "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '错误：发布管理需要 Python 3.10+。' >&2
    exit 1
fi
exec python3 "$release_directory/deploy/release_cli.py" "$@"
