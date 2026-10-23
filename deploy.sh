#!/usr/bin/env bash
set -euo pipefail

CORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CORE_CONFIG="${CORE_ENV_FILE:-$CORE_ROOT/deploy/core/.env.core}"

usage() {
  cat <<'USAGE'
Usage: ./deploy.sh [--env-file NEW_CORE_FILE] COMMAND [COMPOSE_ARGS...]

  init       Create fresh local HTTP configuration; never overwrite a file
  build      Build only the new platform images
  up         Start the new platform in the background and wait for health
  down       Stop the new platform; keep persistent volumes
  config     Validate/render the new Compose configuration (contains secrets)
  ps|logs|exec|run|pull|stop|start|restart
             Forward to the isolated new Compose project

The default configuration is deploy/core/.env.core. Root .env is never loaded.
Create an administrator explicitly: ./deploy.sh exec api python -m zhiheng create-admin --username admin
USAGE
}

if [[ "${1:-}" == '--env-file' ]]; then
  if [[ $# -lt 3 || -z "$2" ]]; then
    usage >&2
    exit 2
  fi
  CORE_CONFIG="$2"
  shift 2
fi

CORE_COMMAND="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi
case "$CORE_COMMAND" in
  help|-h|--help)
    usage
    exit 0
    ;;
  init)
    if [[ $# -ne 0 ]]; then
      usage >&2
      exit 2
    fi
    python3 - "$CORE_CONFIG" <<'PY'
import base64
import os
from pathlib import Path
import secrets
import sys

target = Path(sys.argv[1]).expanduser().resolve()
target.parent.mkdir(parents=True, exist_ok=True)
content = f'''# Independent local HTTP instance. Keep this file private.
CORE_DB_NAME=zhiheng_core
CORE_DB_USER=zhiheng_core
CORE_DB_PASSWORD={secrets.token_urlsafe(32)}
CORE_CREDENTIAL_KEY={base64.urlsafe_b64encode(os.urandom(32)).decode()}
CORE_WEB_BIND=127.0.0.1
CORE_WEB_PORT=19080
CORE_COOKIE_SECURE=false
CORE_ALLOWED_ORIGINS=["http://127.0.0.1:19080"]
CORE_TOKEN_LIMIT=100000
CORE_PROVIDER_TIMEOUT_SECONDS=60
CORE_WORKER_QUEUE=zhiheng:core:queue
'''
try:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit(f'Refusing to overwrite existing core configuration: {target}') from None
with os.fdopen(descriptor, 'w') as stream:
    stream.write(content)
print(f'Created independent local configuration: {target}')
PY
    exit 0
    ;;
  build|up|down|config|ps|logs|exec|run|pull|stop|start|restart)
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if [[ ! -f "$CORE_CONFIG" ]]; then
  printf 'New core env file is missing: %s\nRun ./deploy.sh init first, or pass --env-file NEW_CORE_FILE.\n' "$CORE_CONFIG" >&2
  exit 2
fi
CORE_ENV_FILE="$(cd -- "$(dirname -- "$CORE_CONFIG")" && pwd)/$(basename -- "$CORE_CONFIG")"
export CORE_ENV_FILE
export COMPOSE_DISABLE_ENV_FILE=1

if [[ "$CORE_COMMAND" == up ]]; then
  exec docker compose --env-file "$CORE_ENV_FILE" --file "$CORE_ROOT/compose.yaml" --project-name zhiheng-core up --detach --wait "$@"
fi
exec docker compose --env-file "$CORE_ENV_FILE" --file "$CORE_ROOT/compose.yaml" --project-name zhiheng-core "$CORE_COMMAND" "$@"
