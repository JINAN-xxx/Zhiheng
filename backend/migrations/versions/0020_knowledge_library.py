"""Independent knowledge materials, conservative legacy migration and retrieval defaults."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0020_knowledge_library'
down_revision = '0019_developer_key_deletion'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'knowledge_bases', sa.Column('document_policy', sa.String(16), nullable=False, server_default='legacy')
    )
    op.add_column('knowledge_uploads', sa.Column('origin_file_id', sa.Uuid(), nullable=True))
    op.add_column('knowledge_documents', sa.Column('origin_file_id', sa.Uuid(), nullable=True))
    op.create_index('ix_knowledge_documents_origin_file_id', 'knowledge_documents', ['origin_file_id'])
    op.create_index(
        'uq_knowledge_document_origin',
        'knowledge_documents',
        ['base_id', 'origin_file_id'],
        unique=True,
        postgresql_where=sa.text('origin_file_id IS NOT NULL AND deleted_at IS NULL'),
    )
    op.add_column(
        'knowledge_documents',
        sa.Column('migration_source_document_id', sa.Uuid(), sa.ForeignKey('knowledge_documents.id'), nullable=True),
    )
    op.add_column(
        'knowledge_generations', sa.Column('retrieval_version', sa.Integer(), nullable=False, server_default='1')
    )
    op.create_table(
        'knowledge_defaults',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('default_embed_model_id', sa.Uuid(), sa.ForeignKey('models.id'), nullable=True),
        sa.Column('default_rerank_model_id', sa.Uuid(), sa.ForeignKey('models.id'), nullable=True),
        sa.Column('chunk_size', sa.Integer(), nullable=False, server_default='1000'),
        sa.Column('chunk_overlap', sa.Integer(), nullable=False, server_default='200'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(sa.text('INSERT INTO knowledge_defaults (id) VALUES (1)'))
    _graph_step_constraints(rerank=True)
    op.create_table(
        'knowledge_materials',
        sa.Column('file_id', sa.Uuid(), sa.ForeignKey('files.id'), primary_key=True),
        sa.Column('base_id', sa.Uuid(), sa.ForeignKey('knowledge_bases.id'), nullable=False),
        sa.Column('document_id', sa.Uuid(), sa.ForeignKey('knowledge_documents.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_knowledge_materials_base_id', 'knowledge_materials', ['base_id'])
    op.create_index('ix_knowledge_materials_document_id', 'knowledge_materials', ['document_id'])
    op.create_table(
        'knowledge_migrations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('base_id', sa.Uuid(), sa.ForeignKey('knowledge_bases.id'), nullable=False),
        sa.Column('job_id', sa.Uuid(), sa.ForeignKey('knowledge_jobs.id'), nullable=False, unique=True),
        sa.Column('acl_version', sa.Integer(), nullable=False),
        sa.Column('documents', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_knowledge_migrations_base_id', 'knowledge_migrations', ['base_id'])


def _graph_step_constraints(*, rerank: bool) -> None:
    constraints = sa.inspect(op.get_bind()).get_check_constraints('knowledge_graph_steps')
    for field, values in (
        ('role', ['extract', 'keyword', 'query', 'vlm', 'embedding']),
        ('operation', ['generation', 'keyword', 'embedding']),
    ):
        for constraint in constraints:
            if field in constraint['sqltext']:
                op.drop_constraint(constraint['name'], 'knowledge_graph_steps', type_='check')
        if rerank:
            values.append('rerank')
        permitted = ','.join(repr(value) for value in values)
        op.create_check_constraint(
            f'ck_knowledge_graph_steps_{field}', 'knowledge_graph_steps', f'{field} IN ({permitted})'
        )


def downgrade() -> None:
    _graph_step_constraints(rerank=False)
    op.drop_table('knowledge_migrations')
    op.drop_table('knowledge_materials')
    op.drop_table('knowledge_defaults')
    op.drop_column('knowledge_generations', 'retrieval_version')
    op.drop_column('knowledge_documents', 'migration_source_document_id')
    op.drop_index('uq_knowledge_document_origin', 'knowledge_documents')
    op.drop_index('ix_knowledge_documents_origin_file_id', 'knowledge_documents')
    op.drop_column('knowledge_documents', 'origin_file_id')
    op.drop_column('knowledge_uploads', 'origin_file_id')
    op.drop_column('knowledge_bases', 'document_policy')
