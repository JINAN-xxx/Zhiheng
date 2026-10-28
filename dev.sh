#!/usr/bin/env bash
set -euo pipefail
SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_CONFIG="$SOURCE_ROOT/deploy/core/.env.core"
if SOURCE_DEPLOYED_ENV="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' zhiheng-core-api-1 2>/dev/null)"; then
  if [[ -f "$SOURCE_DEPLOYED_ENV" ]]; then SOURCE_CONFIG="$SOURCE_DEPLOYED_ENV"; fi
fi
if [[ "${1:-}" == '--env-file' ]]; then
  SOURCE_CONFIG="${2:?Missing environment file}"
  shift 2
fi
SOURCE_COMMAND="${1:-up}"
if [[ $# -gt 0 ]]; then shift; fi
if [[ ! -f "$SOURCE_CONFIG" ]]; then
  printf 'Core environment file is missing: %s\nPass --env-file for the existing instance.\n' "$SOURCE_CONFIG" >&2
  exit 1
fi
export CORE_SOURCE_ROOT="$SOURCE_ROOT"
export CORE_SOURCE_PROXY_CONFIG="$SOURCE_ROOT/deploy/core/.source/nginx.conf"
export CORE_ENV_FILE="$(realpath "$SOURCE_CONFIG")"
python "$SOURCE_ROOT/tools/source_proxy.py" "$SOURCE_ROOT/frontend/nginx.core.conf" "$CORE_SOURCE_PROXY_CONFIG"
SOURCE_COMPOSE=(docker compose --env-file "$CORE_ENV_FILE" --file "$SOURCE_ROOT/compose.yaml" --file "$SOURCE_ROOT/deploy/core/compose.source.yaml" --project-name zhiheng-core)
case "$SOURCE_COMMAND" in
  up) exec "${SOURCE_COMPOSE[@]}" up --detach --wait --no-build "$@" ;;
  migrate) exec "${SOURCE_COMPOSE[@]}" run --rm --no-deps migrator ;;
  restart-worker) exec "${SOURCE_COMPOSE[@]}" restart worker ;;
  *) exec "${SOURCE_COMPOSE[@]}" "$SOURCE_COMMAND" "$@" ;;
esac
