#!/bin/sh
set -eu

script_directory=$(CDPATH= cd "$(dirname "$0")" && pwd)
repository_root=$(CDPATH= cd "$script_directory/../.." && pwd)
platform_environment=${1:-"$repository_root/.env"}
database_sql="$repository_root/zhiheng-fastapi-backend/sql/zhiheng-fastapi-pg.sql"
postgres_container=${RUOYI_SHARED_POSTGRES_CONTAINER:-litellm-db}
docker_command=${DOCKER_COMMAND:-docker}
database_name=zhiheng-fastapi
database_user=zhiheng
bootstrap_marker=zhiheng_bootstrap_state
temporary_file=
lock_file="${platform_environment}.lock"
skill_data_directory="$repository_root/data/agent-skills"

cleanup() {
    if [ -n "$temporary_file" ] && [ -f "$temporary_file" ]; then
        rm -f "$temporary_file"
    fi
}
trap cleanup 0 1 2 15

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

initialize_skill_storage() {
    umask 077
    mkdir -p "$skill_data_directory"
    chmod 700 "$skill_data_directory"
}

acquire_platform_lock() {
    environment_directory=$(dirname "$platform_environment")
    [ -d "$environment_directory" ] || fail "Platform environment directory does not exist."
    command -v flock >/dev/null 2>&1 || fail "flock is required to initialize platform secrets safely."
    umask 077
    exec 9>"$lock_file"
    chmod 600 "$lock_file"
    flock 9
}

read_platform_value() {
    environment_key=$1
    if [ ! -f "$platform_environment" ]; then
        return
    fi
    awk -v expected_key="$environment_key" '
        $0 ~ "^[[:space:]]*" expected_key "[[:space:]]*=" {
            sub("^[[:space:]]*" expected_key "[[:space:]]*=[[:space:]]*", "")
            value = $0
        }
        END { print value }
    ' "$platform_environment"
}

write_platform_secrets() {
    environment_directory=$(dirname "$platform_environment")
    [ -d "$environment_directory" ] || fail "Platform environment directory does not exist."
    umask 077
    temporary_file=$(mktemp "$platform_environment.tmp.XXXXXX")
    if [ -f "$platform_environment" ]; then
        awk '$0 !~ /^[[:space:]]*(RUOYI_DB_PASSWORD|RUOYI_REDIS_PASSWORD|JWT_SECRET_KEY)[[:space:]]*=/' \
            "$platform_environment" > "$temporary_file"
    fi
    printf 'RUOYI_DB_PASSWORD=%s\n' "$zhiheng_password" >> "$temporary_file"
    printf 'RUOYI_REDIS_PASSWORD=%s\n' "$redis_password" >> "$temporary_file"
    printf 'JWT_SECRET_KEY=%s\n' "$jwt_secret" >> "$temporary_file"
    chmod 600 "$temporary_file"
    mv "$temporary_file" "$platform_environment"
    temporary_file=
}

initialize_skill_storage
[ -f "$database_sql" ] || fail "ZhiHeng PostgreSQL baseline SQL was not found."
container_running=$($docker_command inspect -f '{{.State.Running}}' "$postgres_container" 2>/dev/null || true)
[ "$container_running" = 'true' ] || fail "Shared PostgreSQL container is not running."
acquire_platform_lock

zhiheng_password=$(read_platform_value RUOYI_DB_PASSWORD)
redis_password=$(read_platform_value RUOYI_REDIS_PASSWORD)
jwt_secret=$(read_platform_value JWT_SECRET_KEY)
secrets_changed=false
if [ -z "$zhiheng_password" ]; then
    zhiheng_password=$(openssl rand -hex 24)
    secrets_changed=true
fi
if [ -z "$jwt_secret" ]; then
    jwt_secret=$(openssl rand -hex 32)
    secrets_changed=true
fi
if [ -z "$redis_password" ]; then
    redis_password=$(openssl rand -hex 24)
    secrets_changed=true
fi
if [ "$secrets_changed" = true ]; then
    write_platform_secrets
else
    chmod 600 "$platform_environment"
fi

case "$zhiheng_password" in
    *[!0-9a-f]*|'') fail "RUOYI_DB_PASSWORD must be a lowercase hexadecimal value." ;;
esac
[ "${#zhiheng_password}" -eq 48 ] || fail "RUOYI_DB_PASSWORD must contain exactly 48 characters."
case "$redis_password" in
    *[!0-9a-f]*|'') fail "RUOYI_REDIS_PASSWORD must be a lowercase hexadecimal value." ;;
esac
[ "${#redis_password}" -eq 48 ] || fail "RUOYI_REDIS_PASSWORD must contain exactly 48 characters."
case "$jwt_secret" in
    *[!0-9a-f]*|'') fail "JWT_SECRET_KEY must be a lowercase hexadecimal value." ;;
esac
[ "${#jwt_secret}" -eq 64 ] || fail "JWT_SECRET_KEY must contain exactly 64 characters."

if ! {
    printf "\\set db_password '%s'\n" "$zhiheng_password"
    cat <<'SQL'
SELECT format('CREATE ROLE zhiheng LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zhiheng')
\gexec
ALTER ROLE zhiheng LOGIN PASSWORD :'db_password';
SELECT 'CREATE DATABASE "zhiheng-fastapi" OWNER zhiheng'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'zhiheng-fastapi')
\gexec
ALTER DATABASE "zhiheng-fastapi" OWNER TO zhiheng;
SQL
} | $docker_command exec -i "$postgres_container" \
    psql -v ON_ERROR_STOP=1 -U litellm -d postgres >/dev/null 2>&1; then
    fail "Failed to create or update the ZhiHeng database role."
fi

bootstrap_relation=$(
    $docker_command exec "$postgres_container" \
        psql -v ON_ERROR_STOP=1 -U litellm -d "$database_name" -Atc \
        "select to_regclass('public.$bootstrap_marker');"
) || fail "Failed to inspect the ZhiHeng database schema."
bootstrap_state=
if [ "$bootstrap_relation" = "$bootstrap_marker" ] || [ "$bootstrap_relation" = "public.$bootstrap_marker" ]; then
    bootstrap_state=$(
        $docker_command exec "$postgres_container" \
            psql -v ON_ERROR_STOP=1 -U litellm -d "$database_name" -Atc \
            "select case when exists (select 1 from public.$bootstrap_marker WHERE marker_id = 1) then 'complete' else '' end;"
    ) || fail "Failed to validate the ZhiHeng database bootstrap marker."
fi

if [ "$bootstrap_state" != 'complete' ]; then
    public_table_count=$(
        $docker_command exec "$postgres_container" \
            psql -v ON_ERROR_STOP=1 -U litellm -d "$database_name" -Atc \
            "select count(*) from pg_catalog.pg_tables where schemaname = 'public';"
    ) || fail "Failed to inspect existing ZhiHeng database tables."
    case "$public_table_count" in
        ''|*[!0-9]*) fail "Shared PostgreSQL returned an invalid public table count." ;;
    esac
    [ "$public_table_count" -eq 0 ] || \
        fail "ZhiHeng database has tables without a completed bootstrap marker; refusing destructive baseline import."

    if ! {
        cat "$database_sql"
        cat <<'SQL'

CREATE TABLE public.zhiheng_bootstrap_state (
    marker_id smallint PRIMARY KEY CHECK (marker_id = 1),
    completed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO public.zhiheng_bootstrap_state (marker_id) VALUES (1);
SQL
    } | $docker_command exec -i "$postgres_container" \
        psql -v ON_ERROR_STOP=1 --single-transaction \
        -U "$database_user" -d "$database_name" >/dev/null; then
        fail "Failed to import the ZhiHeng PostgreSQL baseline."
    fi
    printf '%s\n' 'ZhiHeng PostgreSQL baseline imported.'
else
    printf '%s\n' 'ZhiHeng PostgreSQL bootstrap marker exists; import skipped.'
fi

printf '%s\n' 'ZhiHeng shared PostgreSQL is ready.'
