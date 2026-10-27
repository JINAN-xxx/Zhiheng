"""Knowledge metadata, immutable generations and durable coordinator facts."""
from alembic import op

revision = "0007_knowledge"
down_revision = "0006_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE work_messages ADD COLUMN knowledge_sources JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE chat_messages ADD COLUMN knowledge_sources JSONB NOT NULL DEFAULT '[]'")
    op.execute('CREATE TABLE knowledge_retrieval_intents (\n\tid UUID NOT NULL, \n\tnamespace_id UUID NOT NULL, \n\tclient_request_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tkey_id UUID, \n\tapplication_id UUID, \n\tresource_type VARCHAR(24) NOT NULL, \n\tresource_id UUID NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\trequest JSONB NOT NULL, \n\tdependencies JSONB NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tphase VARCHAR(24) NOT NULL, \n\thits JSONB NOT NULL, \n\tmain_run_id UUID, \n\terror JSONB, \n\tdeadline TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\ttick_token UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(main_run_id) REFERENCES runtime_runs (id), \n\tUNIQUE (namespace_id, client_request_id)\n)')
    op.execute('CREATE INDEX ix_knowledge_retrieval_intents_status ON knowledge_retrieval_intents (status)')
    op.execute('CREATE INDEX ix_knowledge_retrieval_intents_resource_id ON knowledge_retrieval_intents (resource_id)')
    op.execute('CREATE TABLE knowledge_steps (\n\tid UUID NOT NULL, \n\tparent_id UUID NOT NULL, \n\tstep_key VARCHAR(200) NOT NULL, \n\trun_id UUID NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (run_id), \n\tFOREIGN KEY(run_id) REFERENCES runtime_runs (id), \n\tUNIQUE (parent_id, step_key)\n)')
    op.execute('CREATE INDEX ix_knowledge_steps_parent_id ON knowledge_steps (parent_id)')
    op.execute('CREATE TABLE knowledge_bases (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tdescription TEXT NOT NULL, \n\taccess VARCHAR(16) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tacl_version INTEGER NOT NULL, \n\tembed_model_id UUID NOT NULL, \n\tchunk_size INTEGER NOT NULL, \n\tchunk_overlap INTEGER NOT NULL, \n\tactive_revision UUID, \n\tactive_job_id UUID, \n\tlast_error JSONB, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id) REFERENCES identity_users (id), \n\tFOREIGN KEY(embed_model_id) REFERENCES models (id)\n)')
    op.execute('CREATE INDEX ix_knowledge_bases_owner_id ON knowledge_bases (owner_id)')
    op.execute('CREATE TABLE knowledge_folders (\n\tid UUID NOT NULL, \n\tbase_id UUID NOT NULL, \n\tparent_id UUID, \n\tname VARCHAR(100) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(parent_id) REFERENCES knowledge_folders (id), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id)\n)')
    op.execute('CREATE INDEX ix_knowledge_folders_base_id ON knowledge_folders (base_id)')
    op.execute('CREATE TABLE knowledge_generations (\n\tid UUID NOT NULL, \n\tbase_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tcollection_name VARCHAR(160) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tbinding JSONB NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tdimension INTEGER, \n\trow_count INTEGER, \n\tdocument_versions JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (collection_name), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id)\n)')
    op.execute('CREATE INDEX ix_knowledge_generations_base_id ON knowledge_generations (base_id)')
    op.execute('CREATE TABLE knowledge_grants (\n\tbase_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\teffect VARCHAR(8) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (base_id, user_id), \n\tFOREIGN KEY(user_id) REFERENCES identity_users (id), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id)\n)')
    op.execute('CREATE TABLE knowledge_jobs (\n\tid UUID NOT NULL, \n\tbase_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\tclient_request_id UUID NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\toperation VARCHAR(24) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tphase VARCHAR(24) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tindex_revision UUID NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\ttick_token UUID, \n\tdocuments JSONB NOT NULL, \n\terror JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id), \n\tUNIQUE (actor_id, client_request_id), \n\tFOREIGN KEY(actor_id) REFERENCES identity_users (id)\n)')
    op.execute('CREATE INDEX ix_knowledge_jobs_base_id ON knowledge_jobs (base_id)')
    op.execute('CREATE TABLE knowledge_documents (\n\tid UUID NOT NULL, \n\tbase_id UUID NOT NULL, \n\tfile_id UUID NOT NULL, \n\tfolder_id UUID, \n\tfile_name VARCHAR(255) NOT NULL, \n\tfile_size INTEGER NOT NULL, \n\tmime VARCHAR(100) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tactive_revision UUID, \n\tjob_id UUID, \n\tartifact_file_id UUID, \n\tsource_sha256 VARCHAR(64) NOT NULL, \n\tchunk_count INTEGER, \n\tchunk_size INTEGER NOT NULL, \n\tchunk_overlap INTEGER NOT NULL, \n\terror JSONB, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(artifact_file_id) REFERENCES files (id), \n\tFOREIGN KEY(file_id) REFERENCES files (id), \n\tFOREIGN KEY(folder_id) REFERENCES knowledge_folders (id), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id)\n)')
    op.execute('CREATE INDEX ix_knowledge_documents_base_id ON knowledge_documents (base_id)')
    op.execute('CREATE TABLE knowledge_generation_pins (\n\tintent_id UUID NOT NULL, \n\tgeneration_id UUID NOT NULL, \n\tPRIMARY KEY (intent_id, generation_id), \n\tFOREIGN KEY(intent_id) REFERENCES knowledge_retrieval_intents (id), \n\tFOREIGN KEY(generation_id) REFERENCES knowledge_generations (id)\n)')
    op.execute('CREATE TABLE knowledge_uploads (\n\tid UUID NOT NULL, \n\tbase_id UUID NOT NULL, \n\tfile_id UUID NOT NULL, \n\tfolder_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (file_id), \n\tFOREIGN KEY(base_id) REFERENCES knowledge_bases (id), \n\tFOREIGN KEY(folder_id) REFERENCES knowledge_folders (id), \n\tFOREIGN KEY(file_id) REFERENCES files (id)\n)')
    op.execute('CREATE INDEX ix_knowledge_uploads_base_id ON knowledge_uploads (base_id)')
    op.execute('CREATE TABLE knowledge_chunks (\n\tgeneration_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tdocument_id UUID NOT NULL, \n\tdocument_revision UUID NOT NULL, \n\tfile_id UUID NOT NULL, \n\tordinal INTEGER NOT NULL, \n\tcontent TEXT NOT NULL, \n\tcontent_hash VARCHAR(64) NOT NULL, \n\tmodel_text TEXT NOT NULL, \n\tvector JSONB, \n\tPRIMARY KEY (generation_id, id), \n\tFOREIGN KEY(document_id) REFERENCES knowledge_documents (id), \n\tFOREIGN KEY(generation_id) REFERENCES knowledge_generations (id), \n\tFOREIGN KEY(file_id) REFERENCES files (id)\n)')
    op.execute('CREATE INDEX ix_knowledge_chunks_document_id ON knowledge_chunks (document_id)')
    op.execute("ALTER TABLE knowledge_generations ADD COLUMN gc_after TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.drop_column("work_messages", "knowledge_sources")
    op.drop_column("chat_messages", "knowledge_sources")
    op.drop_table('knowledge_chunks')
    op.drop_table('knowledge_uploads')
    op.drop_table('knowledge_generation_pins')
    op.drop_table('knowledge_documents')
    op.drop_table('knowledge_jobs')
    op.drop_table('knowledge_grants')
    op.drop_table('knowledge_generations')
    op.drop_table('knowledge_folders')
    op.drop_table('knowledge_bases')
    op.drop_table('knowledge_steps')
    op.drop_table('knowledge_retrieval_intents')
