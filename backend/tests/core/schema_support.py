"""Compare active schema strictly while retaining the explicitly retired historical tables."""

from typing import Any

from alembic.migration import MigrationContext
from sqlalchemy.engine import Connection

ARCHIVED_TABLES = frozenset(
    {
        'identity_menus',
        'identity_posts',
        'identity_user_posts',
        'operations_dictionaries',
        'operations_dictionary_items',
    }
)


# Frozen migration0040 preserves accepted assistant configuration in the Run
# audit before explicitly retiring the publication catalog.
RETIRED_TABLES = frozenset({'agent_versions'})
RENAMED_COLUMNS = {
    'work_members': {'published_version': 'agent_revision'},
    'work_messages': {'published_version': 'agent_revision'},
}


# Frozen migrations0036/0039 explicitly removed these values. Everything else
# in an original row remains part of the preservation assertion.
RETIRED_COLUMNS = {
    'agents': frozenset({'publication_count', 'published_version'}),
    'agent_run_audits': frozenset({'config_version', 'published_version'}),
    'assistant_conversations': frozenset({'published_version'}),
    'assistant_turns': frozenset({'published_version'}),
    'developer_accounts': frozenset({'limit_tokens', 'quota_version'}),
    'chat_messages': frozenset({'knowledge_sources'}),
    'chat_context_states': frozenset({'target_covered_count', 'dirty', 'summary_task_id', 'last_run_id'}),
    'chat_context_snapshots': frozenset(
        {'summary_task_id', 'summary_version', 'estimated_tokens', 'input_budget', 'dropped_turns'}
    ),
    'chat_summary_tasks': frozenset({'previous_task_id', 'retry_request_id'}),
    'knowledge_graph_attempts': frozenset({'elapsed_seconds'}),
}


def include_active_object(obj: Any, name: str | None, kind: str, reflected: bool, compare_to: Any) -> bool:
    table = obj if kind == 'table' else getattr(obj, 'table', None)
    return not (reflected and getattr(table, 'name', None) in ARCHIVED_TABLES)


def active_migration_context(connection: Connection) -> MigrationContext:
    return MigrationContext.configure(connection, opts={'include_object': include_active_object})


def assert_original_rows(before: list[str], after: list[str], table: str) -> None:
    """All original values survive; new schema columns and explicitly seeded skill rows are validated separately."""
    import json

    old = [json.loads(row) for row in before]
    new = [json.loads(row) for row in after]
    retired = RETIRED_COLUMNS.get(table, frozenset())
    assert all(not (retired & set(row)) for row in new), table
    old = [{key: value for key, value in row.items() if key not in retired} for row in old]
    renamed = RENAMED_COLUMNS.get(table, {})
    old = [{renamed.get(key, key): value for key, value in row.items()} for row in old]
    if table == 'developer_keys' and new and 'grant_family_id' in new[0]:
        # 0044 retires the verifier and grants, retaining the original billing identity.
        migrated = {row['id']: row for row in new}
        for row in old:
            current = migrated[row['id']]
            assert current['deleted_at'] and current['status'] == 'revoked'
            expected = {
                'status': 'revoked', 'digest': 'retired:' + row['id'], 'prefix': '',
                'retire_at': None, 'model_ids': [], 'scopes': [],
                'deleted_at': row.get('deleted_at') or current['deleted_at'],
            }
            row.update({key: value for key, value in expected.items() if key in row})
    if table in {'agents', 'assistant_conversations'}:
        for row in old:
            if isinstance(row.get('config'), dict):
                row['config'].pop('history_rounds', None)
    if table == 'chat_messages':
        for row in new:
            context = row.get('request_context')
            assert not isinstance(context, dict) or 'knowledge_base_ids' not in context, table
        for row in old:
            context = row.get('request_context')
            if isinstance(context, dict):
                context.pop('knowledge_base_ids', None)
    growing = table in {'skills', 'skill_revisions', 'skill_legacy_bindings'}
    if not old:
        assert growing or not new, table
        return
    keys = set(old[0])
    assert all(set(row) == keys for row in old), table
    if growing:
        identity = ['id'] if 'id' in keys else ['owner_id', 'legacy_skill_id']
        original_ids = {tuple(row[key] for key in identity) for row in old}
        new = [row for row in new if tuple(row[key] for key in identity) in original_ids]
    assert all(keys <= set(row) for row in new), table
    expected = sorted(json.dumps(row, sort_keys=True) for row in old)
    actual = sorted(json.dumps({key: row[key] for key in keys}, sort_keys=True) for row in new)
    assert actual == expected, table


def assert_private_base_backfill(before: list[str], after: list[str]) -> None:
    import json
    from datetime import datetime

    old = {json.loads(row)['id']: json.loads(row) for row in before}
    new = {json.loads(row)['id']: json.loads(row) for row in after}
    assert set(old) == set(new)
    for identifier, original in old.items():
        current = new[identifier]
        assert current.pop('access') == 'private'
        assert original.pop('access') == 'public'
        assert current.pop('acl_version') == original.pop('acl_version') + 1
        assert datetime.fromisoformat(current.pop('updated_at')) >= datetime.fromisoformat(original.pop('updated_at'))
        assert current == original
