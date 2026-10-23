from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.models.public import ConnectionInfo, ModelInfo
from zhiheng.runtime.contracts import RunSnapshot

router = APIRouter(prefix='/api/models', tags=['models'])


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {'http', 'https'}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError('A plain HTTP(S) provider endpoint is required')
        _ = parts.port
        return value.rstrip('/')


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    connection_id: UUID
    upstream_model: str = Field(min_length=1, max_length=200)
    max_output_tokens: int = Field(default=512, ge=1, le=8192)


class ModelTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: UUID


@router.get('/connections')
async def connections(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[ConnectionInfo]:
    return await container.models.list_connections(session, principal)


@router.post('/connections', status_code=201)
async def create_connection(
    body: ConnectionRequest, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ConnectionInfo:
    async with session.begin():
        return await container.models.create_connection(
            session, principal, name=body.name, base_url=body.base_url, api_key=body.api_key.get_secret_value()
        )


@router.get('')
async def models(
    container: ContainerDependency, _principal: PrincipalDependency, session: SessionDependency
) -> list[ModelInfo]:
    return await container.models.list_models(session)


@router.post('', status_code=201)
async def create_model(
    body: ModelRequest, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ModelInfo:
    async with session.begin():
        return await container.models.create_model(session, principal, **body.model_dump())


@router.post('/{model_id}/test', status_code=202)
async def test_model(
    model_id: UUID,
    body: ModelTestRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    async with session.begin():
        return await container.admission.admit_test(
            session,
            principal,
            model_id=model_id,
            client_request_id=body.client_request_id,
            token_limit=container.settings.token_limit,
        )
