# Knowledge Integration Plan

- [x] Import the source knowledge library into normal package namespaces; replace unsafe model metadata handling, implicit index deletion, swallowed errors and arbitrary-path ingestion. Add meaningful storage/retrieval tests and optional dependency setup.
- [x] Add authenticated `/ai/knowledge` management APIs and model-runtime adapters, with bounded upload/index operations and safe metadata responses.
- [x] Build native knowledge management/query/graph UI using existing components and ECharts; preserve platform shell and agent editor behavior. Test business failures and unsafe display strings.
- [x] Register menus/permissions and connect selected knowledge bases to the agent provider, including current owner permissions, source identity and context bounds. Add regression tests.
- [x] Connect ordinary chat to the same retrieval boundary, persist selected knowledge base IDs per message in migration 009, and retain skill selection and streaming behavior.
- [ ] Verify the integrated branch, commit source provenance, fast-forward/push `release/platform-integration`, and update the running environment where dependencies and model configuration permit real verification. Preserve root worktree changes and active application settings.

Source and interface decisions are documented in `../specs/2026-09-08-knowledge-integration.md`. The integration worktree is `.worktrees/knowledge-integration`, based on local/remote `3dd71ac`.
