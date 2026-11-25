#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

usage() {
  printf '%s\n' \
    'Usage: bash tools/verify-core.sh COMMAND [ARGS...]' \
    '  foundation           Backend lint, typing, boundaries, export policy and unit tests' \
    '  wheel OUTPUT_DIR     Build and verify the backend wheel' \
    '  backend-integration  Backend integration lint and tests with isolated services' \
    '  workspace-sandbox    Release acceptance with a separate real sandbox; all cases must pass' \
    '  frontend             Frontend unit tests, lint, typing and production build' \
    '  browser ARGS...      Run browser regressions against prepared fixture hosts'
}

COMMAND="${1:-help}"
if (( $# )); then shift; fi

require_test_services() {
  local variable value
  for variable in "$@"; do
    value="${!variable:-}"
    if [[ -z "${value//[[:space:]]/}" ]]; then
      printf 'Isolated integration service is required: %s\n' "$variable" >&2
      exit 2
    fi
  done
}

storage_services=(
  ZHIHENG_TEST_DATABASE_URL ZHIHENG_TEST_REDIS_URL
  ZHIHENG_TEST_S3_ENDPOINT ZHIHENG_TEST_S3_ACCESS_KEY ZHIHENG_TEST_S3_SECRET_KEY
)

case "$COMMAND" in
  foundation)
    cd "$ROOT/backend"
    python -m ruff check --config ruff.toml src tests/unit ../tools
    python -m ruff format --check --config ruff.toml src tests/unit ../tools
    python -m mypy --config-file pyproject.toml
    MYPYPATH=src python -m mypy --config-file pyproject.toml ../tools/export_openapi.py
    python ../tools/check_architecture.py src/zhiheng
    python ../tools/export_source.py --check
    python -m pytest tests/unit -q
    ;;
  wheel)
    if (( $# != 1 )); then usage >&2; exit 2; fi
    OUTPUT_DIR="$(realpath -m -- "$1")"
    cd "$ROOT/backend"
    python -m pip wheel --no-deps --no-build-isolation --wheel-dir "$OUTPUT_DIR" .
    python ../tools/check_core_wheel.py "$OUTPUT_DIR" --source-root src
    ;;
  backend-integration)
    require_test_services "${storage_services[@]}" ZHIHENG_TEST_MILVUS_URI
    cd "$ROOT/backend"
    python -m ruff check --config ruff.toml src tests/core
    python -m ruff format --check --config ruff.toml src tests/core
    python -m pytest tests/core -q -rs
    ;;
  workspace-sandbox)
    if (( $# )); then usage >&2; exit 2; fi
    require_test_services "${storage_services[@]}" \
      ZHIHENG_TEST_SANDBOX_URL ZHIHENG_TEST_SANDBOX_API_KEY ZHIHENG_TEST_SANDBOX_IMAGE
    if [[ -n "${ZHIHENG_TEST_LIVE_MODEL_CONFIG:-}" ]]; then
      printf 'Workspace acceptance requires the local model fixture. Unset ZHIHENG_TEST_LIVE_MODEL_CONFIG.\n' >&2
      exit 2
    fi
    cd "$ROOT/backend"
    PYTEST_ADDOPTS= python -m pytest -p tests.core.sandbox_release_gate -q -rs \
      tests/core/integration/test_workspace_sandbox.py \
      tests/core/integration/test_work_agent_execution.py::test_real_office_artifacts_preview_and_immutable_continuation \
      tests/core/integration/test_work_agent_execution.py::test_agent_repairs_rejected_empty_output_and_publishes_successfully
    ;;
  frontend)
    cd "$ROOT/frontend"
    npm run test:core
    npm run lint:core
    npm run format:check
    npm run typecheck
    npm run build:platform
    ;;
  browser)
    exec python "$ROOT/frontend/tests/browser/run_regressions.py" "$@"
    ;;
  help|--help|-h) usage ;;
  *) usage >&2; exit 2 ;;
esac
