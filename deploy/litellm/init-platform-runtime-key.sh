#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
deploy_env_file=${LITELLM_DEPLOY_ENV_FILE:-"$script_dir/.env"}
platform_env_file=${1:-"$repository_root/.env"}
process_management_key=${LITELLM_MANAGEMENT_KEY:-}
process_litellm_port=${LITELLM_PORT:-}
process_admin_base_url=${LITELLM_ADMIN_BASE_URL:-}

if [ ! -f "$deploy_env_file" ]; then
    printf 'LiteLLM deployment environment file not found: %s\n' "$deploy_env_file" >&2
    exit 1
fi

DOTENV_VALUE=''
read_dotenv_value() {
    dotenv_file=$1
    dotenv_key=$2
    DOTENV_VALUE=''

    if [ ! -r "$dotenv_file" ]; then
        printf 'Cannot read dotenv file required for %s.\n' "$dotenv_key" >&2
        return 1
    fi

    if ! DOTENV_VALUE=$(awk -v expected_key="$dotenv_key" '
        BEGIN {
            prefix = expected_key "="
            single_quote = sprintf("%c", 39)
            found = ""
            malformed = 0
        }
        index($0, prefix) == 1 {
            raw = substr($0, length(prefix) + 1)
            first = substr(raw, 1, 1)
            last = substr(raw, length(raw), 1)
            if (first == single_quote || first == "\"") {
                if (length(raw) < 2 || last != first) {
                    malformed = 1
                    exit
                }
                value = substr(raw, 2, length(raw) - 2)
                if (index(value, first) != 0) {
                    malformed = 1
                    exit
                }
                found = value
            } else {
                if (last == single_quote || last == "\"") {
                    malformed = 1
                    exit
                }
                found = raw
            }
        }
        END {
            if (malformed) {
                exit 2
            }
            printf "%s", found
        }
    ' "$dotenv_file"); then
        DOTENV_VALUE=''
        printf 'Cannot safely parse dotenv value for %s.\n' "$dotenv_key" >&2
        return 1
    fi
}

validate_key_value() {
    key_value=$1
    key_name=$2
    allow_empty=$3

    if [ -z "$key_value" ] && [ "$allow_empty" != true ]; then
        printf '%s is missing.\n' "$key_name" >&2
        return 1
    fi
    if [ "${#key_value}" -gt 4096 ]; then
        printf '%s exceeds the supported length.\n' "$key_name" >&2
        return 1
    fi
    case "$key_value" in
        *'
'* | *''* | *\'* | *\\* | *';'* | *'&'* | *'|'* | *'<'* | *'>'* | *'`'* | *'('* | *')'*)
            printf '%s contains unsupported characters.\n' "$key_name" >&2
            return 1
            ;;
    esac
}

append_platform_keys() {
    output_file=$1
    output_runtime_key=$2
    output_management_key=$3

    printf "LITELLM_RUNTIME_KEY='%s'\n" "$output_runtime_key" >> "$output_file" || return 1
    printf "LITELLM_MANAGEMENT_KEY='%s'\n" "$output_management_key" >> "$output_file" || return 1
}

read_dotenv_value "$deploy_env_file" LITELLM_MASTER_KEY || exit 1
deploy_master_key=$DOTENV_VALUE
read_dotenv_value "$deploy_env_file" LITELLM_MANAGEMENT_KEY || exit 1
deploy_management_key=$DOTENV_VALUE
read_dotenv_value "$deploy_env_file" LITELLM_PORT || exit 1
deploy_litellm_port=$DOTENV_VALUE
read_dotenv_value "$deploy_env_file" LITELLM_ADMIN_BASE_URL || exit 1
deploy_admin_base_url=$DOTENV_VALUE

if [ -e "$platform_env_file" ] || [ -L "$platform_env_file" ]; then
    if [ ! -f "$platform_env_file" ] || [ -L "$platform_env_file" ]; then
        printf 'Platform environment target must be a regular file.\n' >&2
        exit 1
    fi
    read_dotenv_value "$platform_env_file" LITELLM_RUNTIME_KEY || exit 1
    existing_runtime_key=$DOTENV_VALUE
    read_dotenv_value "$platform_env_file" LITELLM_MANAGEMENT_KEY || exit 1
    existing_management_key=$DOTENV_VALUE
else
    existing_runtime_key=''
    existing_management_key=''
fi

if [ -n "$existing_management_key" ]; then
    management_key=$existing_management_key
elif [ -n "$process_management_key" ]; then
    management_key=$process_management_key
else
    management_key=$deploy_management_key
fi

validate_key_value "$management_key" LITELLM_MANAGEMENT_KEY true || exit 1
if [ -n "$existing_runtime_key" ]; then
    validate_key_value "$existing_runtime_key" LITELLM_RUNTIME_KEY false || exit 1
fi

platform_env_dir=$(dirname -- "$platform_env_file")
if [ ! -d "$platform_env_dir" ]; then
    printf 'Platform environment parent directory does not exist.\n' >&2
    exit 1
fi

umask 077
base_file=$(mktemp "${platform_env_file}.base.XXXXXX") || {
    printf 'Cannot stage the Platform environment base file.\n' >&2
    exit 1
}
temporary_file=''
trap 'rm -f "$base_file" ${temporary_file:+"$temporary_file"}' EXIT HUP INT TERM
temporary_file=$(mktemp "${platform_env_file}.tmp.XXXXXX") || {
    printf 'Cannot stage the Platform environment output file.\n' >&2
    exit 1
}

if [ -f "$platform_env_file" ]; then
    if ! awk '!/^LITELLM_RUNTIME_KEY=/ && !/^LITELLM_MANAGEMENT_KEY=/' \
        "$platform_env_file" > "$base_file"; then
        printf 'Cannot read the existing Platform environment file.\n' >&2
        exit 1
    fi
fi
chmod 600 "$base_file" "$temporary_file"

if ! cp "$base_file" "$temporary_file"; then
    printf 'Cannot stage the Platform environment output.\n' >&2
    exit 1
fi
preflight_runtime_key=${existing_runtime_key:-sk-platform-runtime-preflight}
if ! append_platform_keys "$temporary_file" "$preflight_runtime_key" "$management_key"; then
    printf 'Cannot stage Platform credentials.\n' >&2
    exit 1
fi
chmod 600 "$temporary_file"

runtime_key=$existing_runtime_key
runtime_key_created=false
if [ -z "$runtime_key" ]; then
    validate_key_value "$deploy_master_key" LITELLM_MASTER_KEY false || exit 1

    if [ -n "$process_litellm_port" ]; then
        litellm_port=$process_litellm_port
    elif [ -n "$deploy_litellm_port" ]; then
        litellm_port=$deploy_litellm_port
    else
        litellm_port=14000
    fi
    case "$litellm_port" in
        *[!0-9]* | '')
            printf '%s\n' 'LITELLM_PORT must be a decimal port number.' >&2
            exit 1
            ;;
    esac
    if [ "${#litellm_port}" -gt 5 ] || [ "$litellm_port" -lt 1 ] || [ "$litellm_port" -gt 65535 ]; then
        printf '%s\n' 'LITELLM_PORT is outside the supported range.' >&2
        exit 1
    fi

    if [ -n "$process_admin_base_url" ]; then
        admin_base_url=$process_admin_base_url
    elif [ -n "$deploy_admin_base_url" ]; then
        admin_base_url=$deploy_admin_base_url
    else
        admin_base_url="http://127.0.0.1:$litellm_port"
    fi
    case "$admin_base_url" in
        http://* | https://*) ;;
        *)
            printf '%s\n' 'LITELLM_ADMIN_BASE_URL must use http or https.' >&2
            exit 1
            ;;
    esac
    case "$admin_base_url" in
        *'
'* | *''* | *' '* | *';'* | *'&'* | *'|'* | *'`'* | *'('* | *')'*)
            printf '%s\n' 'LITELLM_ADMIN_BASE_URL contains unsupported characters.' >&2
            exit 1
            ;;
    esac

    python_command=$(command -v python3 || command -v python || true)
    if [ -z "$python_command" ]; then
        printf '%s\n' 'python3 or python is required to parse the LiteLLM response.' >&2
        exit 1
    fi

    request_body='{"key_alias":"platform-runtime","key_type":"llm_api","models":[]}'
    if ! response=$(
        curl --silent --show-error --fail-with-body \
            --request POST \
            --header "Authorization: Bearer $deploy_master_key" \
            --header 'Content-Type: application/json' \
            --data "$request_body" \
            "$admin_base_url/key/generate"
    ); then
        printf '%s\n' 'Failed to create the Platform Runtime Key.' >&2
        exit 1
    fi

    if ! runtime_key=$(
        LITELLM_RUNTIME_RESPONSE=$response "$python_command" -c '
import json
import os

payload = json.loads(os.environ["LITELLM_RUNTIME_RESPONSE"])
key = payload.get("key") if isinstance(payload, dict) else None
if not isinstance(key, str) or not key:
    raise SystemExit(1)
print(key, end="")
'
    ); then
        printf '%s\n' 'LiteLLM returned an invalid Runtime Key response.' >&2
        exit 1
    fi
    validate_key_value "$runtime_key" LITELLM_RUNTIME_KEY false || exit 1
    runtime_key_created=true

    if ! cp "$base_file" "$temporary_file"; then
        printf 'Cannot prepare the final Platform environment output.\n' >&2
        exit 1
    fi
    if ! append_platform_keys "$temporary_file" "$runtime_key" "$management_key"; then
        printf 'Cannot write final Platform credentials.\n' >&2
        exit 1
    fi
    chmod 600 "$temporary_file"
fi

if ! mv "$temporary_file" "$platform_env_file"; then
    printf 'Cannot replace the Platform environment file.\n' >&2
    exit 1
fi
temporary_file=''
rm -f "$base_file"
base_file=''
trap - EXIT HUP INT TERM

if [ "$runtime_key_created" = true ]; then
    printf 'Created Platform Runtime Key and synchronized Platform credentials in: %s\n' "$platform_env_file"
else
    printf 'Kept Platform Runtime Key and synchronized Platform credentials in: %s\n' "$platform_env_file"
fi
