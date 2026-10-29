from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from zhiheng.api.contracts import EmptyRequest
from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.models.public import ConnectionInfo, ModelInfo, RateCreate, RateInfo
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
    max_output_tokens: int = Field(default=512, strict=True, ge=1, le=131072)
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)
    published: bool = True


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
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    include_disabled: bool = False,
) -> list[ModelInfo]:
    return await container.models.list_models(session, principal=principal, include_disabled=include_disabled)


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


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    enabled: bool | None = None

    @field_validator('base_url')
    @classmethod
    def check_url(cls, value: str | None) -> str | None:
        return ConnectionRequest.validate_base_url(value) if value is not None else None


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=80)
    connection_id: UUID | None = None
    upstream_model: str | None = Field(default=None, min_length=1, max_length=200)
    max_output_tokens: int | None = Field(default=None, strict=True, ge=1, le=131072)
    enabled: bool | None = None
    vision_enabled: bool | None = Field(default=None, strict=True)
    published: bool | None = None


@router.patch('/connections/{connection_id}')
async def update_connection(
    connection_id: UUID,
    body: ConnectionPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConnectionInfo:
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if body.api_key is not None:
        fields['api_key'] = body.api_key.get_secret_value()
    async with session.begin():
        return await container.models.update_connection(session, principal, connection_id, fields)


@router.delete('/connections/{connection_id}')
async def delete_connection(
    connection_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ConnectionInfo:
    async with session.begin():
        return await container.model_management.delete_connection(session, principal, connection_id)


@router.post('/connections/{connection_id}/{action}')
async def connection_action(
    connection_id: UUID,
    action: Literal['enable', 'disable'],
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> ConnectionInfo:
    async with session.begin():
        return await container.models.update_connection(
            session, principal, connection_id, {'enabled': action == 'enable'}
        )


@router.patch('/{model_id}')
async def update_model(
    model_id: UUID,
    body: ModelPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ModelInfo:
    async with session.begin():
        return await container.models.update_model(
            session, principal, model_id, body.model_dump(exclude_unset=True, exclude_none=True)
        )


@router.delete('/{model_id}')
async def delete_model(
    model_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ModelInfo:
    async with session.begin():
        return await container.model_management.delete_model(session, principal, model_id)


@router.get('/{model_id}/rate-cards')
async def rates(
    model_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[RateInfo]:
    return await container.models.rates(session, principal, model_id)


@router.post('/{model_id}/rate-cards', status_code=201)
async def create_rate(
    model_id: UUID,
    body: RateCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RateInfo:
    async with session.begin():
        return await container.models.add_rate(session, principal, model_id, body)


@router.post('/{model_id}/{action}')
async def model_action(
    model_id: UUID,
    action: Literal['enable', 'disable', 'publish', 'unpublish'],
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> ModelInfo:
    fields: dict[str, object] = (
        {'enabled': action == 'enable'} if action in {'enable', 'disable'} else {'published': action == 'publish'}
    )
    async with session.begin():
        return await container.models.update_model(session, principal, model_id, fields)
