#!/usr/bin/env bash
# Hold WSL open for the local platform, independently of the launching terminal.
set -euo pipefail
umask 077
platform_script="$(readlink -f "${BASH_SOURCE[0]}")"
platform_state="/tmp/zhiheng-core-host-${UID}"
if [[ -e "$platform_state" && ( -L "$platform_state" || ! -O "$platform_state" ) ]]; then
  echo 'Unexpected platform host state directory owner.' >&2
  exit 1
fi
mkdir -p "$platform_state"
chmod 700 "$platform_state"
platform_pid_file="$platform_state/host.pid"
platform_running() {
  [[ -r "$platform_pid_file" ]] || return 1
  read -r platform_pid < "$platform_pid_file"
  [[ "$platform_pid" =~ ^[0-9]+$ && -r "/proc/$platform_pid/cmdline" ]] || return 1
  [[ "$(tr '\0' ' ' < "/proc/$platform_pid/cmdline")" == *"$platform_script"* ]]
}
case "${1:-start}" in
  status)
    if platform_running; then echo 'Platform host running.'; else echo 'Platform host stopped.'; exit 1; fi
    exit 0
    ;;
  stop)
    if platform_running; then kill -TERM "$platform_pid"; fi
    flock -w 5 "$platform_state/host.lock" true
    exit 0
    ;;
  start) ;;
  *) echo 'Usage: keep-platform-wsl.sh [start|status|stop]' >&2; exit 2 ;;
esac
exec 9> "$platform_state/host.lock"
flock -n 9 || exit 0
printf '%s\n' "$$" > "$platform_pid_file"
platform_sleep_pid=''
platform_cleanup() {
  if [[ -n "$platform_sleep_pid" ]]; then kill "$platform_sleep_pid" 2>/dev/null || true; fi
  rm -f -- "$platform_pid_file"
}
trap platform_cleanup EXIT
trap 'exit 0' TERM INT HUP
for platform_attempt in {1..60}; do
  if docker info >/dev/null 2>&1; then break; fi
  sleep 1
done
docker info >/dev/null
for platform_container in \
  zhiheng-core-postgres-1 zhiheng-core-redis-1 \
  zhiheng-core-vector-etcd-1 zhiheng-core-vector-objects-1 zhiheng-core-milvus-1 \
  zhiheng-core-worker-1 zhiheng-core-api-1 zhiheng-core-ui-source-1 zhiheng-core-web-1; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$platform_container")" != true ]]; then
    docker start "$platform_container" >/dev/null
  fi
done
sleep infinity &
platform_sleep_pid=$!
wait "$platform_sleep_pid"
