#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file=${1:-"$script_dir/.env"}

if [ -e "$env_file" ]; then
    printf 'Keeping existing environment file: %s\n' "$env_file"
    exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
    printf '%s\n' 'openssl is required to generate LiteLLM secrets.' >&2
    exit 1
fi

umask 077
db_password=$(openssl rand -hex 24)
redis_password=$(openssl rand -hex 24)
master_key="sk-$(openssl rand -hex 32)"
salt_key="sk-$(openssl rand -hex 32)"

{
    printf '%s\n' 'LITELLM_IMAGE=ghcr.io/berriai/litellm-database:v1.95.0'
    printf '%s\n' 'LITELLM_POSTGRES_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/postgres:16.14-alpine3.24'
    printf '%s\n' 'LITELLM_REDIS_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/redis:7.4.7'
    printf '%s\n' 'LITELLM_PORT=14000'
    printf 'LITELLM_DB_PASSWORD=%s\n' "$db_password"
    printf 'LITELLM_REDIS_PASSWORD=%s\n' "$redis_password"
    printf 'LITELLM_MASTER_KEY=%s\n' "$master_key"
    printf 'LITELLM_SALT_KEY=%s\n' "$salt_key"
} > "$env_file"

printf 'Created environment file: %s\n' "$env_file"
