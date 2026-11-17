"""Release duplicate confirmed upload payloads; keep immutable revisions and receipts."""

from alembic import op

revision = '0038_skill_receipt_payload'
down_revision = '0037_model_capacity_note'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE skill_drafts SET package_bytes = decode('', 'hex')
        WHERE confirmed_info IS NOT NULL AND jsonb_typeof(confirmed_info) = 'object'
          AND confirmed_skill_id IS NOT NULL AND confirmed_revision_id IS NOT NULL
          AND confirmed_info->>'id' = confirmed_skill_id::text
          AND confirmed_info->>'revision_id' = confirmed_revision_id::text
          AND confirmed_info ?& ARRAY['id','slug','name','description','package_version','version',
              'status','source','revision_id','content_hash','file_count','total_bytes','created_at','updated_at']
          AND octet_length(package_bytes) > 0"""
    )


def downgrade() -> None:
    # Earlier applications also replay confirmed_info without parsing the draft.
    # Duplicate upload bytes are not reconstructed; immutable revision packages remain intact.
    pass
