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

New knowledge bases use Milvus; install requirements-knowledge-milvus.txt,
also included by the PostgreSQL graph dependencies. requirements-knowledge.txt
remains for legacy Chroma and additional document parsers. Models are published
platform models; vector stores receive explicit vectors and do not download
an embedding model. Missing dependencies produce explicit 503
responses. Lightweight TXT/Markdown/CSV/JSON parsing uses Python standard
libraries; PDF, DOCX, HTML and XLSX load their parsers only for those formats.
Scanned PDFs and image OCR are not enabled by this extraction.

For Milvus, configure KNOWLEDGE_MILVUS_URI (or MILVUS_URI), optionally
KNOWLEDGE_MILVUS_TOKEN and KNOWLEDGE_MILVUS_DB_NAME, then install
requirements-knowledge-milvus.txt. Do not install SDK model/offline/API extras.
Graph construction/exploration uses LightRAG 1.5.7 with PGTableGraphStorage,
PGKVStorage and PGDocStatusStorage on ordinary PostgreSQL, plus
MilvusVectorDBStorage for graph vectors. It requires no AGE, pgvector or Neo4j.
Install requirements-knowledge-graph.txt (included by requirements-pg.txt).
The adapter reuses the platform DB_HOST, DB_PORT, DB_USERNAME, DB_PASSWORD and
DB_DATABASE settings when DB_TYPE=postgresql. Explicit POSTGRES_HOST,
POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DATABASE values
have priority. Missing MILVUS_URI/TOKEN/DB_NAME aliases are populated from
KNOWLEDGE_MILVUS_URI/TOKEN/DB_NAME; the Milvus database defaults to default.
These server settings are never copied into knowledge metadata or API errors.
The runtime also requires the verified o200k tokenizer cache, configured with
TIKTOKEN_CACHE_DIR (or the standard tiktoken cache directory). Missing or
corrupt cache data fails immediately; graph initialization never downloads
tokenizer assets. Loading and warming the tokenizer runs outside the event loop.

Dockerfile.pg prepares this verified resource at build time. If container builds
cannot reach the vocabulary host, prepare it on the build host first (the cache
directory is ignored by Git and copied into the image by the Docker build):

```bash
cd ruoyi-fastapi-backend
python scripts/prepare_graph_tokenizer.py --cache-dir tokenizer-cache
```

The preparation command has a bounded download timeout and verifies SHA-256;
it also accepts `--source-cache /path/to/verified/cache` for offline preparation.

Use exactly one backend worker (APP_WORKERS=1): LightRAG pipeline coordination
and buffered writes remain process-local even with remote durable storage.
Every KB has its own PostgreSQL workspace and Milvus collection names.
Leave POSTGRES_WORKSPACE and MILVUS_WORKSPACE unset; global workspace overrides
are rejected because they would collapse independent KBs. PostgreSQL tables
are created automatically using the configured database user's permissions.
Vector management and metadata mutations use cross-process advisory locks.

The graph page can enable and build an existing Milvus KB with a published
chat model. Building is asynchronous and records per-document progress,
failures and interruption state. Repeated start requests reuse the running job;
retry resumes unfinished documents without recomputing the primary vector index.
Graph failures leave successfully indexed vector documents available. Failed
or interrupted graph documents are cleaned before retry. Reading a graph never
starts extraction implicitly. Embedding and chat calls use ModelService,
bounded concurrency and finite timeouts; graph vectors validate the original
published embedding model, binding fingerprint and dimension before each call.
Returned model/binding snapshots are checked again to reject a binding change
between resolution and embedding.

Uploaded content is stored under the configured knowledge data directory using
opaque KB/upload IDs. JSON metadata uses atomic replacement and filesystem
locks; it contains platform model IDs, index fingerprints/dimensions, and
document/chunk manifests, never runtime credentials. Keep this directory on one
persistent local filesystem shared by backend workers. Back up metadata/uploads
and the associated Milvus and PostgreSQL stores together. Closing/restarting
the backend releases clients without dropping the stored graph. Deleting a KB
clears only its graph workspace, document state and LLM cache. Its three
Milvus graph vector collections are permanently dropped through the public SDK
after model queues, pending writes and clients are stopped; they are not
recreated or loaded by the LightRAG clear-collection operation.

Querying a changed model binding/dimension reports KNOWLEDGE_REINDEX_REQUIRED
without replacing a collection. Recreate the KB explicitly when changing its
embedding model/index specification. Document rechunking retains its stable
document ID and records replacement chunk IDs before touching the vector store.
Failed/interrupted documents can be retried through rechunk or removed; their
content is excluded from retrieval, and incomplete graph state is unavailable.

Agent context comes from the bounded primary vector index and verified
document/chunk manifests. LightRAG generated answers remain management query
output and are never substituted for Agent passages.

Validation: adapter/configuration tests cover published model callbacks,
workspace isolation, initialization/cancellation cleanup, retries and deletion.
The installed LightRAG 1.5.7 API test constructs the real PostgreSQL/Milvus
storage classes and invokes the real embedding wrapper without external
connections. Infrastructure validation must also exercise PostgreSQL, Milvus
and the published models together in an isolated KB.
