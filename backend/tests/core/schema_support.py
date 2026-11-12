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
