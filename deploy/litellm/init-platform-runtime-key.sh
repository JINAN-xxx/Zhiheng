#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
deploy_env_file=${LITELLM_DEPLOY_ENV_FILE:-"$script_dir/.env"}
platform_env_file=${1:-"$repository_root/.env"}

if [ ! -f "$deploy_env_file" ]; then
    printf 'LiteLLM deployment environment file not found: %s\n' "$deploy_env_file" >&2
    exit 1
fi

set -a
. "$deploy_env_file"
set +a

if [ -z "${LITELLM_MASTER_KEY:-}" ]; then
    printf '%s\n' 'LITELLM_MASTER_KEY is missing from the deployment environment.' >&2
    exit 1
fi

if [ -f "$platform_env_file" ]; then
    existing_runtime_key=$(sed -n 's/^LITELLM_RUNTIME_KEY=//p' "$platform_env_file" | tail -n 1)
    if [ -n "$existing_runtime_key" ]; then
        chmod 600 "$platform_env_file"
        printf 'Keeping existing Platform environment file: %s\n' "$platform_env_file"
        exit 0
    fi
fi

python_command=$(command -v python3 || command -v python || true)
if [ -z "$python_command" ]; then
    printf '%s\n' 'python3 or python is required to parse the LiteLLM response.' >&2
    exit 1
fi

litellm_port=${LITELLM_PORT:-14000}
admin_base_url=${LITELLM_ADMIN_BASE_URL:-"http://127.0.0.1:$litellm_port"}
request_body='{"key_alias":"platform-runtime","key_type":"llm_api","models":[]}'

if ! response=$(
    curl --silent --show-error --fail-with-body \
        --request POST \
        --header "Authorization: Bearer $LITELLM_MASTER_KEY" \
        --header 'Content-Type: application/json' \
        --data "$request_body" \
        "$admin_base_url/key/generate"
); then
    printf '%s\n' 'Failed to create the Platform Runtime Key.' >&2
    exit 1
fi

if ! runtime_key=$(
    printf '%s' "$response" | "$python_command" -c '
import json
import sys

payload = json.load(sys.stdin)
key = payload.get("key") if isinstance(payload, dict) else None
if not isinstance(key, str) or not key:
    raise SystemExit(1)
print(key, end="")
'
); then
    printf '%s\n' 'LiteLLM returned an invalid Runtime Key response.' >&2
    exit 1
fi

umask 077
temporary_file=$(mktemp "${platform_env_file}.tmp.XXXXXX")
trap 'rm -f "$temporary_file"' EXIT HUP INT TERM

if [ -f "$platform_env_file" ]; then
    sed '/^LITELLM_RUNTIME_KEY=/d' "$platform_env_file" > "$temporary_file"
fi
printf 'LITELLM_RUNTIME_KEY=%s\n' "$runtime_key" >> "$temporary_file"
chmod 600 "$temporary_file"
mv "$temporary_file" "$platform_env_file"
trap - EXIT HUP INT TERM

printf 'Created Platform Runtime Key in: %s\n' "$platform_env_file"
