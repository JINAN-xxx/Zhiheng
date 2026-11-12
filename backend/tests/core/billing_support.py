"""Explicitly enable metering for tests of reservations, separately from disabled-mode coverage."""

from uuid import uuid4

import httpx


async def enable_free_billing(admin: httpx.AsyncClient) -> None:
    models = await admin.get('/api/models?include_disabled=true')
    assert models.status_code == 200, models.text
    for model in models.json():
        if not model['enabled']:
            continue
        response = await admin.post(
            '/api/billing/models/' + model['id'] + '/rates',
            json={
                'billing_mode': 'search_unit' if model['model_type'] == 'rerank' else 'token',
                'is_free': True,
                'input_points': '0',
                'output_points': '0',
                'cache_points': '0',
                'search_points': '0',
                'client_request_id': str(uuid4()),
            },
        )
        assert response.status_code == 201, response.text
    state = (await admin.get('/api/billing/state')).json()
    if not state['enabled']:
        response = await admin.patch(
            '/api/billing/state',
            json={
                'enabled': True,
                'expected_version': state['version'],
                'client_request_id': str(uuid4()),
            },
        )
        assert response.status_code == 200, response.text
