# Configurable Agent Implementation Plan

Goal: Deliver configurable agents with draft/publish lifecycle, skill composition, a knowledge retrieval extension contract, trial chat and authenticated public invocation.

Architecture: Agent Extension owns configuration and publication. AI's existing model runtime and public API services own invocation, key validation, model grants, quotas and usage. Frontend reuses Ant Design Vue and existing workspace components.

## Tasks

- [x] Add backend agent schemas, storage, MySQL/PostgreSQL migration, owner-scoped service and management controller. Test lifecycle and ownership with SQLite before implementation; verify stale updates and snapshot isolation.
- [x] Add bounded knowledge-provider protocol and agent invocation service/controller. Test invalid overrides, identity checks, no-provider behavior and composition before implementation. Reuse public API coordinator for JSON/SSE completion.
- [x] Add frontend agent API, list/editor/trial/publish/key controls at `plugins/agent_extension/views/agent/index.vue`. Add navigation and plugin menu permissions. Test transforms and workflow with browser fixtures.
- [x] Add integration documentation, review permission/plugin wiring, run targeted Python tests and frontend plugin tests/build, verify desktop/mobile browser workflows, and start a local development server.

Run backend checks from `zhiheng-fastapi-backend`: `python -m pytest -q tests/plugins/agent_extension` and relevant existing public API tests. Run frontend checks from `zhiheng-fastapi-frontend`: `npm run test:plugin`; build output must use `/tmp/zhiheng-configurable-agent-build` to avoid tracked `dist` changes.

Implementation is authorized in the current branch. Preserve existing deleted mobile-app files and generated build outputs. Do not merge remote divergence or commit unrelated files.

## Validation Progress

- Backend integration: 175 tests passed, including preview and plugin structure checks; full Agent Extension Ruff checks passed.
- Frontend: 28 plugin test groups, including 8 agent transform tests, passed.
- Final production Vite build passed using the temporary output directory after fixing the nested application modal stacking order.
- Independent backend review confirmed router discovery, owner/application isolation and snapshot behavior; its authentication-header finding was corrected.
- Browser workflow passed with 82 mocked backend requests: create/save, application creation, knowledge error recovery, two-turn preview, publication/draft isolation, published-model key grants, revocation and plaintext cleanup.
- Desktop 1440x900 and mobile 390x844 screenshots were inspected; no document overflow or unexpected console/page errors.
- Frontend development server responds at `http://127.0.0.1:12581`. Its existing proxy targets backend port 9099, which is not running locally. Runtime use requires the existing plugin upgrade process and a configured backend/model runtime. Live databases and model providers were not changed or invoked.
