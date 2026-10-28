"""Persistent response generations, message feedback and revocable text shares."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0009_message_actions'
down_revision = '0008_graph'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat_messages', sa.Column('reply_to_message_id', sa.Uuid(), nullable=True))
    op.add_column('chat_messages', sa.Column('generation_index', sa.Integer(), nullable=False, server_default='1'))
    op.add_column(
        'chat_messages',
        sa.Column('request_context', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_foreign_key('fk_chat_reply_message', 'chat_messages', 'chat_messages', ['reply_to_message_id'], ['id'])
    op.create_index('ix_chat_messages_reply_to_message_id', 'chat_messages', ['reply_to_message_id'])
    op.create_check_constraint('ck_chat_generation_index', 'chat_messages', 'generation_index >= 1')
    op.create_unique_constraint(
        'uq_chat_reply_generation', 'chat_messages', ['reply_to_message_id', 'generation_index']
    )
    op.create_table(
        'chat_message_feedback',
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), primary_key=True),
        sa.Column('message_id', sa.Uuid(), sa.ForeignKey('chat_messages.id'), primary_key=True),
        sa.Column('value', sa.String(4), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("value IN ('up', 'down')", name='ck_chat_message_feedback_value'),
    )
    op.create_index('ix_chat_message_feedback_message_id', 'chat_message_feedback', ['message_id'])
    op.create_table(
        'chat_message_shares',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('identity_users.id'), nullable=False),
        sa.Column('message_id', sa.Uuid(), sa.ForeignKey('chat_messages.id'), nullable=False),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('chat_conversations.id'), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('question', sa.Text(), nullable=True),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('include_question', sa.Boolean(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('expires_at > created_at', name='ck_chat_message_shares_expiry'),
    )
    for column in ['owner_id', 'message_id', 'conversation_id']:
        op.create_index('ix_chat_message_shares_' + column, 'chat_message_shares', [column])
    op.create_index(
        'ix_chat_message_shares_owner_message', 'chat_message_shares', ['owner_id', 'message_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_table('chat_message_shares')
    op.drop_table('chat_message_feedback')
    op.drop_constraint('uq_chat_reply_generation', 'chat_messages', type_='unique')
    op.drop_constraint('ck_chat_generation_index', 'chat_messages', type_='check')
    op.drop_index('ix_chat_messages_reply_to_message_id', table_name='chat_messages')
    op.drop_constraint('fk_chat_reply_message', 'chat_messages', type_='foreignkey')
    op.drop_column('chat_messages', 'request_context')
    op.drop_column('chat_messages', 'generation_index')
    op.drop_column('chat_messages', 'reply_to_message_id')
