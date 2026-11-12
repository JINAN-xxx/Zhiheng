"""Graph configuration, fenced attempts and the complete SDK-owned catalog."""

from alembic import op

revision = '0008_graph'
down_revision = '0007_knowledge'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "\nCREATE TABLE knowledge_graph_configs (\n\tbase_id UUID NOT NULL, \n\tversion INTEGER NOT NULL, \n\tllm_model_id UUID NOT NULL, \n\treasoning_strategy VARCHAR(16) NOT NULL, \n\ttimeout_seconds INTEGER NOT NULL, \n\tmax_output_tokens INTEGER NOT NULL, \n\tconcurrency INTEGER NOT NULL, \n\tactive_revision UUID, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (base_id), \n\tCHECK (reasoning_strategy IN ('auto','default','low','disabled')), \n\tCHECK (version>=1 AND concurrency BETWEEN 1 AND 4 AND timeout_seconds BETWEEN 30 AND 600 AND max_output_tokens BETWEEN 1024 AND 32768), \n\tFOREIGN KEY(llm_model_id) REFERENCES models (id), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE knowledge_graph_jobs (\n\tid UUID NOT NULL, \n\tbase_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tclient_request_id UUID NOT NULL, \n\trequest_fingerprint VARCHAR(64) NOT NULL, \n\toperation VARCHAR(16) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tphase VARCHAR(32) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgraph_revision UUID, \n\tfrozen JSONB NOT NULL, \n\trequest JSONB NOT NULL, \n\tprocessed_documents INTEGER, \n\ttotal_documents INTEGER, \n\tprogress JSONB NOT NULL, \n\tresult JSONB, \n\terror JSONB, \n\tcancel_requested_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(actor_id) REFERENCES identity_users (id), \n\tCHECK (status IN ('pending','running','waiting_model','draining','completed','failed','cancelled','interrupted')), \n\tUNIQUE (actor_id, client_request_id), \n\tFOREIGN KEY(owner_id) REFERENCES identity_users (id), \n\tCHECK (operation IN ('build','preview','query','gc')), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE knowledge_graph_attempts (\n\tid UUID NOT NULL, \n\tjob_id UUID NOT NULL, \n\tepoch INTEGER NOT NULL, \n\towner_token UUID, \n\tstatus VARCHAR(24) NOT NULL, \n\tworkspace VARCHAR(96) NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\tdeadline TIMESTAMP WITH TIME ZONE NOT NULL, \n\tnext_ordinal INTEGER NOT NULL, \n\twriter_stopped BOOLEAN NOT NULL, \n\telapsed_seconds FLOAT, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstopped_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tUNIQUE (job_id), \n\tUNIQUE (workspace), \n\tCHECK (NOT writer_stopped OR stopped_at IS NOT NULL), \n\tCHECK (epoch>=1 AND next_ordinal>=0), \n\tCHECK (status IN ('pending','running','draining','completed','failed','cancelled','interrupted')), \n\tFOREIGN KEY(job_id) REFERENCES knowledge_graph_jobs (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE knowledge_graph_generations (\n\tid UUID NOT NULL, \n\tbase_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tmanifest JSONB NOT NULL, \n\tfrozen JSONB NOT NULL, \n\tgc_after TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (attempt_id), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id), \n\tFOREIGN KEY(attempt_id) REFERENCES knowledge_graph_attempts (id), \n\tCHECK (status IN ('candidate','active','retired','collecting','collected'))\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE knowledge_graph_steps (\n\tid UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tepoch INTEGER NOT NULL, \n\trole VARCHAR(24) NOT NULL, \n\toperation VARCHAR(24) NOT NULL, \n\tordinal INTEGER NOT NULL, \n\trun_id UUID NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (run_id), \n\tUNIQUE (attempt_id, role, operation, ordinal), \n\tCHECK (role IN ('extract','keyword','query','vlm','embedding')), \n\tFOREIGN KEY(attempt_id) REFERENCES knowledge_graph_attempts (id), \n\tCHECK (operation IN ('generation','keyword','embedding')), \n\tFOREIGN KEY(run_id) REFERENCES runtime_runs (id), \n\tCHECK (epoch>=1 AND ordinal>=1)\n)\n\n"
    )
    op.execute(
        '\nCREATE TABLE knowledge_graph_pins (\n\tid UUID NOT NULL, \n\tgeneration_id UUID NOT NULL, \n\tjob_id UUID, \n\towner_token UUID NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(job_id) REFERENCES knowledge_graph_jobs (id), \n\tFOREIGN KEY(generation_id) REFERENCES knowledge_graph_generations (id)\n)\n\n'
    )
    op.execute('CREATE INDEX ix_knowledge_graph_jobs_base_id ON knowledge_graph_jobs (base_id)')
    op.execute('CREATE INDEX ix_knowledge_graph_jobs_status ON knowledge_graph_jobs (status)')
    op.execute('CREATE INDEX ix_knowledge_graph_generations_base_id ON knowledge_graph_generations (base_id)')
    op.execute('CREATE INDEX ix_knowledge_graph_steps_attempt_id ON knowledge_graph_steps (attempt_id)')
    op.execute('CREATE INDEX ix_knowledge_graph_pins_generation_id ON knowledge_graph_pins (generation_id)')
    op.execute('CREATE SCHEMA knowledge_graph_sdk')
    op.execute('REVOKE ALL ON SCHEMA knowledge_graph_sdk FROM PUBLIC')
    op.execute('SET LOCAL search_path TO knowledge_graph_sdk,pg_catalog')
    op.execute(
        "CREATE TABLE LIGHTRAG_DOC_FULL (\n                    id VARCHAR(255),\n                    workspace VARCHAR(255),\n                    doc_name VARCHAR(1024),\n                    content TEXT,\n                    meta JSONB,\n                    sidecar_location TEXT NULL,\n                    parse_format VARCHAR(32) NULL DEFAULT 'raw',\n                    -- content_hash is TEXT (not VARCHAR(N)) so the column is\n                    -- agnostic to the hash algorithm. Today's pipeline writes\n                    -- 64-char SHA-256 hex; future algos (SHA-512, base64) do\n                    -- not require a schema change.\n                    content_hash TEXT NULL,\n                    -- process_options is an opaque selector string emitted by\n                    -- sanitize_process_options() (e.g. \"Fi\").\n                    process_options TEXT NULL,\n                    chunk_options JSONB NULL DEFAULT '{}'::jsonb,\n                    parse_engine TEXT NULL,\n                    create_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    update_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n\t                CONSTRAINT LIGHTRAG_DOC_FULL_PK PRIMARY KEY (workspace, id)\n                    )"
    )
    op.execute(
        "CREATE TABLE LIGHTRAG_DOC_CHUNKS (\n                    id VARCHAR(255),\n                    workspace VARCHAR(255),\n                    full_doc_id VARCHAR(256),\n                    chunk_order_index INTEGER,\n                    tokens INTEGER,\n                    content TEXT,\n                    file_path TEXT NULL,\n                    llm_cache_list JSONB NULL DEFAULT '[]'::jsonb,\n                    heading JSONB NULL DEFAULT '{}'::jsonb,\n                    sidecar JSONB NULL DEFAULT '{}'::jsonb,\n                    create_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    update_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n\t                CONSTRAINT LIGHTRAG_DOC_CHUNKS_PK PRIMARY KEY (workspace, id)\n                    )"
    )
    op.execute(
        'CREATE TABLE LIGHTRAG_LLM_CACHE (\n\t                workspace varchar(255) NOT NULL,\n\t                id varchar(255) NOT NULL,\n                    original_prompt TEXT,\n                    return_value TEXT,\n                    chunk_id VARCHAR(255) NULL,\n                    cache_type VARCHAR(32),\n                    queryparam JSONB NULL,\n                    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n\t                CONSTRAINT LIGHTRAG_LLM_CACHE_PK PRIMARY KEY (workspace, id)\n                    )'
    )
    op.execute(
        "CREATE TABLE LIGHTRAG_DOC_STATUS (\n\t               workspace varchar(255) NOT NULL,\n\t               id varchar(255) NOT NULL,\n\t               content_summary varchar(255) NULL,\n\t               content_length int4 NULL,\n\t               chunks_count int4 NULL,\n\t               status varchar(64) NULL,\n\t               file_path TEXT NULL,\n\t               chunks_list JSONB NULL DEFAULT '[]'::jsonb,\n\t               track_id varchar(255) NULL,\n\t               metadata JSONB NULL DEFAULT '{}'::jsonb,\n\t               error_msg TEXT NULL,\n\t               -- content_hash is TEXT (not VARCHAR(N)) so the column is\n\t               -- agnostic to the hash algorithm. Today's pipeline writes\n\t               -- 64-char SHA-256 hex; future algos (SHA-512, base64) do\n\t               -- not require a schema change.\n\t               content_hash TEXT NULL,\n\t               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n\t               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n\t               CONSTRAINT LIGHTRAG_DOC_STATUS_PK PRIMARY KEY (workspace, id)\n\t              )"
    )
    op.execute(
        'CREATE TABLE LIGHTRAG_FULL_ENTITIES (\n                    id VARCHAR(255),\n                    workspace VARCHAR(255),\n                    entity_names JSONB,\n                    count INTEGER,\n                    create_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    update_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    CONSTRAINT LIGHTRAG_FULL_ENTITIES_PK PRIMARY KEY (workspace, id)\n                    )'
    )
    op.execute(
        'CREATE TABLE LIGHTRAG_FULL_RELATIONS (\n                    id VARCHAR(255),\n                    workspace VARCHAR(255),\n                    relation_pairs JSONB,\n                    count INTEGER,\n                    create_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    update_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    CONSTRAINT LIGHTRAG_FULL_RELATIONS_PK PRIMARY KEY (workspace, id)\n                    )'
    )
    op.execute(
        'CREATE TABLE LIGHTRAG_ENTITY_CHUNKS (\n                    id VARCHAR(512),\n                    workspace VARCHAR(255),\n                    chunk_ids JSONB,\n                    count INTEGER,\n                    create_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    update_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    CONSTRAINT LIGHTRAG_ENTITY_CHUNKS_PK PRIMARY KEY (workspace, id)\n                    )'
    )
    op.execute(
        'CREATE TABLE LIGHTRAG_RELATION_CHUNKS (\n                    id VARCHAR(512),\n                    workspace VARCHAR(255),\n                    chunk_ids JSONB,\n                    count INTEGER,\n                    create_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    update_time TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP,\n                    CONSTRAINT LIGHTRAG_RELATION_CHUNKS_PK PRIMARY KEY (workspace, id)\n                    )'
    )
    op.execute(
        "CREATE TABLE lightrag_graph_nodes (\n    workspace   TEXT        NOT NULL,\n    namespace   TEXT        NOT NULL DEFAULT 'chunk_entity_relation',\n    id          TEXT        NOT NULL,\n    properties  JSONB       NOT NULL DEFAULT '{}',\n    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),\n    PRIMARY KEY (workspace, namespace, id)\n);"
    )
    op.execute(
        "CREATE TABLE lightrag_graph_edges (\n    workspace   TEXT        NOT NULL,\n    namespace   TEXT        NOT NULL DEFAULT 'chunk_entity_relation',\n    src_id      TEXT        NOT NULL,\n    tgt_id      TEXT        NOT NULL,\n    properties  JSONB       NOT NULL DEFAULT '{}',\n    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),\n    PRIMARY KEY (workspace, namespace, src_id, tgt_id)\n);"
    )
    op.execute('CREATE INDEX idx_lightrag_doc_full_id ON lightrag_doc_full (id)')
    op.execute('CREATE INDEX idx_lightrag_doc_full_workspace_id ON lightrag_doc_full (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_doc_chunks_id ON lightrag_doc_chunks (id)')
    op.execute('CREATE INDEX idx_lightrag_doc_chunks_workspace_id ON lightrag_doc_chunks (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_llm_cache_id ON lightrag_llm_cache (id)')
    op.execute('CREATE INDEX idx_lightrag_llm_cache_workspace_id ON lightrag_llm_cache (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_doc_status_id ON lightrag_doc_status (id)')
    op.execute('CREATE INDEX idx_lightrag_doc_status_workspace_id ON lightrag_doc_status (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_full_entities_id ON lightrag_full_entities (id)')
    op.execute('CREATE INDEX idx_lightrag_full_entities_workspace_id ON lightrag_full_entities (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_full_relations_id ON lightrag_full_relations (id)')
    op.execute('CREATE INDEX idx_lightrag_full_relations_workspace_id ON lightrag_full_relations (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_entity_chunks_id ON lightrag_entity_chunks (id)')
    op.execute('CREATE INDEX idx_lightrag_entity_chunks_workspace_id ON lightrag_entity_chunks (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_relation_chunks_id ON lightrag_relation_chunks (id)')
    op.execute('CREATE INDEX idx_lightrag_relation_chunks_workspace_id ON lightrag_relation_chunks (workspace, id)')
    op.execute('CREATE INDEX idx_lightrag_doc_status_track_id ON lightrag_doc_status (track_id)')
    op.execute(
        'CREATE INDEX idx_lightrag_doc_status_workspace_status_updated_at ON lightrag_doc_status (workspace, status, updated_at DESC)'
    )
    op.execute(
        'CREATE INDEX idx_lightrag_doc_status_workspace_status_created_at ON lightrag_doc_status (workspace, status, created_at DESC)'
    )
    op.execute(
        'CREATE INDEX idx_lightrag_doc_status_workspace_updated_at ON lightrag_doc_status (workspace, updated_at DESC)'
    )
    op.execute(
        'CREATE INDEX idx_lightrag_doc_status_workspace_created_at ON lightrag_doc_status (workspace, created_at DESC)'
    )
    op.execute('CREATE INDEX idx_lightrag_doc_status_workspace_file_path ON lightrag_doc_status (workspace, file_path)')
    op.execute(
        "CREATE INDEX idx_lightrag_doc_status_workspace_content_hash ON lightrag_doc_status (workspace, content_hash) WHERE content_hash IS NOT NULL AND content_hash <> ''"
    )
    op.execute(
        'CREATE INDEX idx_lightrag_doc_status_ws_status_created_nf_id ON lightrag_doc_status (workspace, status, created_at NULLS FIRST, id)'
    )
    op.execute(
        'CREATE INDEX idx_lightrag_graph_edges_namespace_tgt ON lightrag_graph_edges (workspace, namespace, tgt_id)'
    )
    op.execute(
        'CREATE INDEX idx_lightrag_graph_edges_noncanonical ON lightrag_graph_edges (workspace, namespace) WHERE src_id COLLATE "C" > tgt_id COLLATE "C"'
    )
    op.execute(
        'ALTER TABLE lightrag_graph_edges ADD CONSTRAINT fk_lightrag_graph_edges_src FOREIGN KEY (workspace,namespace,src_id) REFERENCES lightrag_graph_nodes (workspace,namespace,id) ON DELETE CASCADE'
    )
    op.execute(
        'ALTER TABLE lightrag_graph_edges ADD CONSTRAINT fk_lightrag_graph_edges_tgt FOREIGN KEY (workspace,namespace,tgt_id) REFERENCES lightrag_graph_nodes (workspace,namespace,id) ON DELETE CASCADE'
    )
    op.execute('SET LOCAL search_path TO public,pg_catalog')


def downgrade() -> None:
    op.execute('DROP SCHEMA knowledge_graph_sdk CASCADE')
    op.drop_table('knowledge_graph_pins')
    op.drop_table('knowledge_graph_steps')
    op.drop_table('knowledge_graph_generations')
    op.drop_table('knowledge_graph_attempts')
    op.drop_table('knowledge_graph_jobs')
    op.drop_table('knowledge_graph_configs')
