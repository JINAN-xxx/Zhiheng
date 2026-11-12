"""Only the explicit migrator supplies a connection to this version graph."""

from alembic import context

connection = context.config.attributes.get('connection')
if connection is None:
    raise RuntimeError('Run migrations with `python -m zhiheng migrate`')
context.configure(
    connection=connection, transactional_ddl=True, target_metadata=context.config.attributes.get('target_metadata')
)
with context.begin_transaction():
    context.run_migrations()
