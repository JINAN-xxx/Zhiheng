# Knowledge Capability Integration

User authorized bringing the knowledge capabilities from `release/antd-minimal-loop` into `release/platform-integration`. Target is `3dd71ac`; source is `f9762470bce611e4db72988d19becc6ce501bb5e`. Source is an orphan snapshot, and some common platform files have invalid encoding or no line breaks. Only knowledge implementation and necessary integration changes are imported; clean historical `eec58cd7284b9433b10be8e58695148e1c9f870e` documents the intended supporting changes. Existing agent configuration, welcome messages, centered editor, skill selection, public API and platform layout stay on the target implementation.

## Behavior

- Provide a native Vue/Ant Design knowledge page at `/ai/knowledge` with database CRUD, folders, upload/indexing, preview, rechunk/delete, retrieval evaluation and KB-scoped graph exploration.
- Knowledge bases are platform-shared resources governed by `ai:knowledge:list/query/add/edit/remove` permissions. Agent retrieval rechecks the stored agent owner's active user/roles and `ai:knowledge:query` permission on every call, then restricts retrieval to bound KB IDs.
- Model selection uses published, active platform embedding/chat models. Persist identifiers and index specifications only; never expose or persist model credentials in KB metadata. Refresh model authorization/bindings at invocation.
- Missing dependencies, invalid models, failed indexing and unavailable stores produce explicit errors. Queries never delete/recreate an incompatible index. Destructive replacement is only part of an explicit user-requested operation.
- Default to an available vector store; retain the source Chroma/Milvus/LightRAG capabilities through lazy optional imports. Graph retrieval requires configured infrastructure. Agent context must use actual retrieved document/chunk sources; generated graph answers are not substituted for source passages.
- Uploaded files receive opaque upload IDs bound to a KB. Preview/indexing accepts owned IDs, not arbitrary host paths or URLs. Validate KB/document/folder scope and chunk overlap before operations. JSON metadata writes must be atomic and mutation synchronization explicit.
- Existing intelligent-agent `knowledgeBaseIds` is populated by an authorized select list and resolved through `app.state.agent_knowledge_provider`. The welcome message remains display metadata.
- Ordinary chat shares the same authenticated retrieval boundary, preserves skills, and persists `knowledgeBaseIds` per message using migration 009. Both chat and agents receive a bounded user-context reference message rather than promoting retrieved text to system instructions.
- Each upload is limited to 20 MiB. Chunk size is 100–4000 characters; overlap is 0–1000 and less than chunk size. Retrieval returns at most 8 passages within a 24000-byte UTF-8 context budget. LightRAG requires one backend worker and configured Milvus/Neo4j services.

## API Contract

All management routes use platform Bearer authentication and `/ai/knowledge`. Successful responses follow `{code:200,data:payload}`. Preserve source snake_case resource fields to minimize conversion.

| Route | Payload/result |
| --- | --- |
| GET `/databases` | data `{databases:[{db_id,name,description,kb_type,enable_knowledge_graph,embed_model_id,llm_model_id,created_at}]}` |
| GET `/available` | same database list, gated by `ai:knowledge:query` for chat and agent selectors |
| POST `/databases` | `{database_name,description,embed_model_id,kb_type,enable_knowledge_graph,llm_model_id?}` |
| GET/PUT/DELETE `/databases/{db_id}` | safe details; PUT `{name,description}` |
| GET `/models?model_type=embedding|chat` | data `{models:[{model_id,display_name,model_code}]}` |
| GET `/types` | data `{kb_types:{name:{available,...}}}` |
| GET/POST `/folders` | query `db_id`; create `{name,db_id,parent_id?}`; data `{folders:[{id,name,parent_id,db_id}]}` |
| PUT/DELETE `/folders/{id}?db_id=...` | rename `{name}`; explicit recursive delete |
| GET `/databases/{id}/documents` | data `{documents:[{id,file_name,type,status,created_at,file_size,folder_id,chunk_size,chunk_overlap}],total}` |
| POST `/files/upload?db_id=...&folder_id=...` | multipart file; data `{upload_id,file_name,file_size}` |
| POST `/databases/{id}/documents` | `{items:[upload_id],params:{content_type:'file',chunk_size,chunk_overlap}}`; data `{status,items:[{status,...}]}` |
| GET `/databases/{id}/documents/{doc_id}/preview` | data `{content}` |
| DELETE `/databases/{id}/documents/{doc_id}` | deletion result |
| POST `/databases/{id}/documents/{doc_id}/rechunk` | `{chunk_size,chunk_overlap}` |
| POST `/databases/{id}/query` | `{query,meta:{top_k,mode,only_need_context:true|false}}`; data `{status,result}` |
| POST `/databases/{id}/query-graph` | graph query using the same payload, available only with configured graph dependencies |
| GET `/graphs/{id}/labels` | data `{labels}` |
| GET `/graphs/{id}/subgraph` | query `node_label,max_depth,max_nodes`; data `{nodes,edges,is_truncated}` |

No source `.env`, saved documents, graph dumps, logs, compiled assets or caches are imported. No unauthenticated standalone HTML/API is mounted. Selective integration records the source commit without bringing its unrelated runtime data into the target tree.
