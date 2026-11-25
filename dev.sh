#!/usr/bin/env bash
set -euo pipefail
SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_CONFIG=''
SOURCE_PROJECT=''

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

usage() {
  printf '%s\n' \
    'Usage: ./dev.sh [--env-file PATH] [--attach-existing PROJECT] COMMAND [COMPOSE_ARGS...]' \
    'Without --attach-existing, the canonical checkout and env paths select an independent project.' \
    '--attach-existing requires an existing API container mounted from this checkout.' \
    'Commands: project-name, up, migrate, restart-worker, or a Docker Compose command.'
}

while (( $# )); do
  case "$1" in
    --env-file)
      [[ -n "${2:-}" && "$2" != -* ]] || fail 'Missing environment file path'
      SOURCE_CONFIG="$2"
      shift 2
      ;;
    --attach-existing)
      [[ "${2:-}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail 'Missing or invalid Compose project name'
      SOURCE_PROJECT="$2"
      shift 2
      ;;
    --help|-h) usage; exit 0 ;;
    --*) fail "Unknown option: $1" ;;
    *) break ;;
  esac
done

if [[ -n "$SOURCE_PROJECT" ]]; then
  api_ids="$(docker ps -aq \
    --filter "label=com.docker.compose.project=$SOURCE_PROJECT" \
    --filter 'label=com.docker.compose.service=api' \
    --filter 'label=com.docker.compose.oneoff=False')" || fail "Cannot inspect project $SOURCE_PROJECT"
  [[ -n "$api_ids" && "$api_ids" != *$'\n'* ]] || fail "Expected one existing API container in $SOURCE_PROJECT"
  mounted_src="$(docker inspect --format '{{ range .Mounts }}{{ if eq .Destination "/app/src" }}{{ .Source }}{{ end }}{{ end }}' "$api_ids")"
  mounted_migrations="$(docker inspect --format '{{ range .Mounts }}{{ if eq .Destination "/app/migrations" }}{{ .Source }}{{ end }}{{ end }}' "$api_ids")"
  [[ "$mounted_src" == "$SOURCE_ROOT/backend/src" && "$mounted_migrations" == "$SOURCE_ROOT/backend/migrations" ]] ||
    fail "Project $SOURCE_PROJECT is not running source mode from this checkout: $SOURCE_ROOT"
  deployed_config="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' "$api_ids")"
  [[ -f "$deployed_config" && "$deployed_config" == /* ]] || fail "Cannot identify the environment file for $SOURCE_PROJECT"
  if [[ -n "$SOURCE_CONFIG" && "$(realpath -e -- "$SOURCE_CONFIG")" != "$(realpath -e -- "$deployed_config")" ]]; then
    fail "Selected environment file differs from the existing configuration of $SOURCE_PROJECT"
  fi
  SOURCE_CONFIG="$deployed_config"
else
  SOURCE_CONFIG="${SOURCE_CONFIG:-$SOURCE_ROOT/deploy/core/.env.core}"
fi

SOURCE_COMMAND="${1:-up}"
if [[ $# -gt 0 ]]; then shift; fi
if [[ ! -f "$SOURCE_CONFIG" ]]; then
  fail "Core environment file is missing: $SOURCE_CONFIG. Run ./deploy.sh init or pass --env-file."
fi
CORE_ENV_FILE="$(realpath -e -- "$SOURCE_CONFIG")"
export CORE_ENV_FILE
if [[ -z "$SOURCE_PROJECT" ]]; then
  source_id="$(printf '%s\0%s\0' "$SOURCE_ROOT" "$CORE_ENV_FILE" | sha256sum)"
  SOURCE_PROJECT="zhiheng-dev-${source_id:0:16}"
fi
if [[ "$SOURCE_COMMAND" == 'project-name' ]]; then
  printf '%s\n' "$SOURCE_PROJECT"
  exit 0
fi

export CORE_SOURCE_ROOT="$SOURCE_ROOT"
export CORE_SOURCE_PROXY_CONFIG="$SOURCE_ROOT/deploy/core/.source/$SOURCE_PROJECT/nginx.conf"
export COMPOSE_DISABLE_ENV_FILE=1
python3 "$SOURCE_ROOT/tools/source_proxy.py" "$SOURCE_ROOT/frontend/nginx.core.conf" "$CORE_SOURCE_PROXY_CONFIG"
SOURCE_COMPOSE=(docker compose --env-file "$CORE_ENV_FILE" --file "$SOURCE_ROOT/compose.yaml" --file "$SOURCE_ROOT/deploy/core/compose.source.yaml" --project-name "$SOURCE_PROJECT")
if [[ -f "$SOURCE_ROOT/deploy/core/.source/compose.lan.yaml" ]]; then
  SOURCE_COMPOSE+=(--file "$SOURCE_ROOT/deploy/core/.source/compose.lan.yaml")
fi
case "$SOURCE_COMMAND" in
  up) exec "${SOURCE_COMPOSE[@]}" up --detach --wait --no-build "$@" ;;
  migrate) exec "${SOURCE_COMPOSE[@]}" run --rm --no-deps migrator ;;
  restart-worker) exec "${SOURCE_COMPOSE[@]}" restart worker ;;
  *) exec "${SOURCE_COMPOSE[@]}" "$SOURCE_COMMAND" "$@" ;;
esac
