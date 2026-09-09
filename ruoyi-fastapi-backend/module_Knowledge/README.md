# Knowledge integration provenance

Selected parsing/chunking and Chroma/Milvus/LightRAG integration behavior comes
from release/antd-minimal-loop, commit
f9762470bce611e4db72988d19becc6ce501bb5e, especially original
src/knowledge/indexing.py, kb_utils.py, chroma_kb.py, milvus_kb.py,
lightrag_kb.py, kb_manager.py, and knowledge_base.py.

The platform boundary lives in plugins/ai/knowledge/ and
plugins/ai/controller/ai_knowledge_controller.py. This extraction uses normal
package imports and the existing asynchronous ModelService. It does not import
the standalone server, environment loader, credential catalog, saved documents,
compiled files, logs, or graph dumps.

Install optional vector/document support with requirements-knowledge.txt.
Models are published platform models; Chroma receives explicit vectors and does
not download an embedding model. Missing dependencies produce explicit 503
responses. Lightweight TXT/Markdown/CSV/JSON parsing uses Python standard
libraries; PDF, DOCX, HTML and XLSX load their parsers only for those formats.
Scanned PDFs and image OCR are not enabled by this extraction.

For Milvus, configure KNOWLEDGE_MILVUS_URI (or MILVUS_URI), optionally
KNOWLEDGE_MILVUS_TOKEN and KNOWLEDGE_MILVUS_DB_NAME, then install
requirements-knowledge-milvus.txt. Do not install SDK model/offline/API extras.
For graph construction/exploration, install requirements-knowledge-graph.txt
and configure MILVUS_URI, NEO4J_URI,
NEO4J_USERNAME, and NEO4J_PASSWORD; use exactly one backend worker
(APP_WORKERS=1). The LightRAG JSON stores cache workspace data in memory, so
graph capabilities deliberately report unavailable for multiple workers.
Vector management and metadata mutations use cross-process advisory locks.

Uploaded content is stored under the configured knowledge data directory using
opaque KB/upload IDs. JSON metadata uses atomic replacement and filesystem
locks; it contains platform model IDs, index fingerprints/dimensions, and
document/chunk manifests, never runtime credentials. Keep this directory on one
persistent local filesystem shared by backend workers. Back up metadata/uploads
and the associated Chroma or remote vector/graph stores together.

Querying a changed model binding/dimension reports KNOWLEDGE_REINDEX_REQUIRED
without replacing a collection. Recreate the KB explicitly when changing its
embedding model/index specification. Document rechunking retains its stable
document ID and records replacement chunk IDs before touching the vector store.
Failed/interrupted documents can be retried through rechunk or removed; their
content is excluded from retrieval, and incomplete graph state is unavailable.

Agent context comes from the bounded primary vector index and verified
document/chunk manifests. LightRAG generated answers remain management query
output and are never substituted for Agent passages.

Validation: runtime/authorization/controller tests and real Chroma persistence
tests run without external Milvus/Neo4j services. The optional graph adapter API
was checked against LightRAG 1.5.7 and pymilvus 2.6.17; full graph infrastructure
validation is still required in an environment with those services.
