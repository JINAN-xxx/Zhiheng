"""Explicit wire fixture for the financial trace API; no real provider requests."""

def credit_trace(hold):
    invocation = {
        'id': 'invocation-' + hold['run_id'], 'run_id': hold['run_id'], 'user_id': hold['user_id'],
        'model_id': hold['model_id'], 'operation': 'rerank' if hold['billing_mode'] == 'search_unit' else 'chat',
        'source': hold['source'], 'status': 'known' if hold['status'] == 'settled' else 'unknown',
        'version': 5, 'points_enforced': True, 'reserved_tokens': 2000, 'charged_tokens': None,
        'currency': None, 'reserved_cost': None, 'billable_cost': None, 'rate_card_id': None,
        'rate_version': None, 'application_id': None, 'key_id': None, 'created_at': hold['created_at'],
        **{name: hold.get(name) for name in ['input_tokens', 'output_tokens', 'cache_tokens', 'reasoning_tokens', 'search_units']},
    }
    return {'billing': {'invocation': invocation, 'reservation': hold, 'ledger': [],
        'ledger_total': 0, 'ledger_page': 1, 'ledger_page_size': 20}, 'execution': None,
        'related': [], 'related_has_more': False, 'observed_at': '2026-11-14T00:00:00Z'}
