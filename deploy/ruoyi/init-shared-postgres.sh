#!/bin/sh
set -eu

script_directory=$(CDPATH= cd "$(dirname "$0")" && pwd)
repository_root=$(CDPATH= cd "$script_directory/../.." && pwd)
platform_environment=${1:-"$repository_root/.env"}
database_sql="$repository_root/ruoyi-fastapi-backend/sql/ruoyi-fastapi-pg.sql"
postgres_container=${RUOYI_SHARED_POSTGRES_CONTAINER:-litellm-db}
docker_command=${DOCKER_COMMAND:-docker}
database_name=ruoyi-fastapi
database_user=ruoyi
bootstrap_marker=ruoyi_bootstrap_state
temporary_file=

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

read_database_password() {
    if [ ! -f "$platform_environment" ]; then
        return
    fi
    awk '
        /^[[:space:]]*RUOYI_DB_PASSWORD[[:space:]]*=/ {
            sub(/^[[:space:]]*RUOYI_DB_PASSWORD[[:space:]]*=[[:space:]]*/, "")
            value = $0
        }
        END { print value }
    ' "$platform_environment"
}

write_database_password() {
    environment_directory=$(dirname "$platform_environment")
    [ -d "$environment_directory" ] || fail "Platform environment directory does not exist."
    umask 077
    temporary_file=$(mktemp "$platform_environment.tmp.XXXXXX")
    if [ -f "$platform_environment" ]; then
        awk '$0 !~ /^[[:space:]]*RUOYI_DB_PASSWORD[[:space:]]*=/' \
            "$platform_environment" > "$temporary_file"
    fi
    printf 'RUOYI_DB_PASSWORD=%s\n' "$ruoyi_password" >> "$temporary_file"
    chmod 600 "$temporary_file"
    mv "$temporary_file" "$platform_environment"
    temporary_file=
}

[ -f "$database_sql" ] || fail "RuoYi PostgreSQL baseline SQL was not found."
container_running=$($docker_command inspect -f '{{.State.Running}}' "$postgres_container" 2>/dev/null || true)
[ "$container_running" = 'true' ] || fail "Shared PostgreSQL container is not running."

ruoyi_password=$(read_database_password)
if [ -z "$ruoyi_password" ]; then
    ruoyi_password=$(openssl rand -hex 24)
    write_database_password
else
    chmod 600 "$platform_environment"
fi

case "$ruoyi_password" in
    *[!0-9a-f]*|'') fail "RUOYI_DB_PASSWORD must be a lowercase hexadecimal value." ;;
esac
[ "${#ruoyi_password}" -eq 48 ] || fail "RUOYI_DB_PASSWORD must contain exactly 48 characters."

if ! {
    printf "\\set db_password '%s'\n" "$ruoyi_password"
    cat <<'SQL'
SELECT format('CREATE ROLE ruoyi LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ruoyi')
\gexec
ALTER ROLE ruoyi LOGIN PASSWORD :'db_password';
SELECT 'CREATE DATABASE "ruoyi-fastapi" OWNER ruoyi'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ruoyi-fastapi')
\gexec
ALTER DATABASE "ruoyi-fastapi" OWNER TO ruoyi;
SQL
} | $docker_command exec -i "$postgres_container" \
    psql -v ON_ERROR_STOP=1 -U litellm -d postgres >/dev/null 2>&1; then
    fail "Failed to create or update the RuoYi database role."
fi

bootstrap_relation=$(
    $docker_command exec "$postgres_container" \
        psql -v ON_ERROR_STOP=1 -U litellm -d "$database_name" -Atc \
        "select to_regclass('public.$bootstrap_marker');"
) || fail "Failed to inspect the RuoYi database schema."
bootstrap_state=
if [ "$bootstrap_relation" = "$bootstrap_marker" ] || [ "$bootstrap_relation" = "public.$bootstrap_marker" ]; then
    bootstrap_state=$(
        $docker_command exec "$postgres_container" \
            psql -v ON_ERROR_STOP=1 -U litellm -d "$database_name" -Atc \
            "select case when exists (select 1 from public.$bootstrap_marker WHERE marker_id = 1) then 'complete' else '' end;"
    ) || fail "Failed to validate the RuoYi database bootstrap marker."
fi

if [ "$bootstrap_state" != 'complete' ]; then
    public_table_count=$(
        $docker_command exec "$postgres_container" \
            psql -v ON_ERROR_STOP=1 -U litellm -d "$database_name" -Atc \
            "select count(*) from pg_catalog.pg_tables where schemaname = 'public';"
    ) || fail "Failed to inspect existing RuoYi database tables."
    case "$public_table_count" in
        ''|*[!0-9]*) fail "Shared PostgreSQL returned an invalid public table count." ;;
    esac
    [ "$public_table_count" -eq 0 ] || \
        fail "RuoYi database has tables without a completed bootstrap marker; refusing destructive baseline import."

    if ! {
        cat "$database_sql"
        cat <<'SQL'

CREATE TABLE public.ruoyi_bootstrap_state (
    marker_id smallint PRIMARY KEY CHECK (marker_id = 1),
    completed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO public.ruoyi_bootstrap_state (marker_id) VALUES (1);
SQL
    } | $docker_command exec -i "$postgres_container" \
        psql -v ON_ERROR_STOP=1 --single-transaction \
        -U "$database_user" -d "$database_name" >/dev/null; then
        fail "Failed to import the RuoYi PostgreSQL baseline."
    fi
    printf '%s\n' 'RuoYi PostgreSQL baseline imported.'
else
    printf '%s\n' 'RuoYi PostgreSQL bootstrap marker exists; import skipped.'
fi

printf '%s\n' 'RuoYi shared PostgreSQL is ready.'
