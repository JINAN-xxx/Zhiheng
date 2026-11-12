"""Optional resource request keys retain strict UUID transport validation."""

from typing import Annotated
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from zhiheng.api.common.preconditions import idempotency_header
from zhiheng.shared.errors import AppError


@pytest.fixture
def key_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(AppError)
    async def app_error(request: Request, error: AppError) -> JSONResponse:
        return JSONResponse({'code': error.code}, status_code=error.status_code)

    @app.get('/identity')
    async def identity(
        first: Annotated[UUID, Depends(idempotency_header)],
        second: Annotated[UUID, Depends(idempotency_header)],
    ) -> dict[str, str]:
        return {'first': str(first), 'second': str(second)}

    return app


async def test_omission_generates_one_fresh_uuid_per_http_request(key_app: FastAPI) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=key_app), base_url='http://test') as client:
        first, second = await client.get('/identity'), await client.get('/identity')
    assert first.status_code == second.status_code == 200
    assert first.json()['first'] == first.json()['second']
    assert second.json()['first'] == second.json()['second']
    assert UUID(first.json()['first']) != UUID(second.json()['first'])


async def test_provided_uuid_is_preserved(key_app: FastAPI) -> None:
    key = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=key_app), base_url='http://test') as client:
        response = await client.get('/identity', headers={'Idempotency-Key': key})
    assert response.status_code == 200
    assert response.json() == {'first': key, 'second': key}


@pytest.mark.parametrize('values', [[''], ['not-a-uuid'], [str(uuid4()), str(uuid4())]])
async def test_present_invalid_or_duplicate_keys_are_rejected(key_app: FastAPI, values: list[str]) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=key_app), base_url='http://test') as client:
        response = await client.get('/identity', headers=[('Idempotency-Key', value) for value in values])
    assert response.status_code == 422
