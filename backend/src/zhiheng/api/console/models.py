from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.models.public import (
    ChatInputBudget,
    ConnectionInfo,
    ConnectionOption,
    ModelConfiguration,
    ModelInfo,
    ModelTrialInput,
    RateCreate,
    RateInfo,
    chat_input_budget,
)
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.shared.numbers import MAX_SAFE_INTEGER

router = APIRouter(prefix='/api/models', tags=['models'])


class ConsoleModelInfo(ModelInfo):
    chat_input_budget: ChatInputBudget | None


def console_model(model: ModelInfo, platform_input_tokens: int | None) -> ConsoleModelInfo:
    budget = (
        chat_input_budget(
            context_window_tokens=model.context_window_tokens,
            max_output_tokens=model.max_output_tokens,
            platform_input_tokens=platform_input_tokens,
        )
        if model.model_type == 'chat'
        else None
    )
    return ConsoleModelInfo(**model.model_dump(), chat_input_budget=budget)


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


class ModelRequest(ModelConfiguration):
    """The same configuration contract is used by API and internal writes."""


class ModelTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: UUID


@router.get('/connections')
async def connections(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[ConnectionInfo]:
    return await container.models.connections.list(session, principal)


@router.get('/connection-options')
async def connection_options(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[ConnectionOption]:
    return await container.models.connections.options(session, principal)


@router.post('/connections', status_code=201)
async def create_connection(
    body: ConnectionRequest, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ConnectionInfo:
    async with session.begin():
        return await container.models.connections.create(
            session, principal, name=body.name, base_url=body.base_url, api_key=body.api_key.get_secret_value()
        )


@router.get('')
async def models(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    include_disabled: bool = False,
    trial: bool = False,
) -> list[ConsoleModelInfo]:
    models = await container.models.catalog.list(
        session, principal=principal, include_disabled=include_disabled, for_test=trial
    )
    return [console_model(model, container.settings.chat_context_input_tokens) for model in models]


@router.post('', status_code=201)
async def create_model(
    body: ModelRequest, container: ContainerDependency, principal: PrincipalDependency
) -> ConsoleModelInfo:
    model = await container.model_configuration.save(principal, body.model_dump())
    return console_model(model, container.settings.chat_context_input_tokens)


@router.post('/{model_id}/test', status_code=202)
async def test_model(
    model_id: UUID,
    body: ModelTestRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    async with session.begin():
        return await container.model_trials.admit(
            session,
            principal,
            model_id=model_id,
            client_request_id=body.client_request_id,
        )


class ModelTrialRequest(ModelTrialInput):
    client_request_id: UUID


@router.post('/{model_id}/trial', status_code=202)
async def trial_model(
    model_id: UUID,
    body: ModelTrialRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    async with session.begin():
        return await container.model_trials.admit(
            session,
            principal,
            model_id=model_id,
            client_request_id=body.client_request_id,
            trial=ModelTrialInput(prompt=body.prompt, documents=body.documents),
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
    max_output_tokens: int | None = Field(default=None, strict=True, ge=1, le=MAX_SAFE_INTEGER)
    context_window_tokens: int | None = Field(default=None, strict=True, ge=256, le=2097152)
    capacity_note: str | None = Field(default=None, strict=True, max_length=2000)
    enabled: bool | None = Field(default=None, strict=True)
    vision_enabled: bool | None = Field(default=None, strict=True)


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
        return await container.models.connections.update(session, principal, connection_id, fields)


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
        return await container.models.connections.update(
            session, principal, connection_id, {'enabled': action == 'enable'}
        )


@router.patch('/{model_id}')
async def update_model(
    model_id: UUID,
    body: ModelPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConsoleModelInfo:
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if 'context_window_tokens' in body.model_fields_set:
        fields['context_window_tokens'] = body.context_window_tokens
    if fields == {'enabled': False}:
        async with session.begin():
            return console_model(
                await container.models.catalog.update(session, principal, model_id, fields),
                container.settings.chat_context_input_tokens,
            )
    return console_model(
        await container.model_configuration.save(principal, fields, model_id),
        container.settings.chat_context_input_tokens,
    )


@router.delete('/{model_id}')
async def delete_model(
    model_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    retire_key_access: bool = False,
) -> ConsoleModelInfo:
    async with session.begin():
        return console_model(
            await container.model_management.delete_model(
                session, principal, model_id, retire_key_access=retire_key_access
            ),
            container.settings.chat_context_input_tokens,
        )


@router.get('/{model_id}/rate-cards')
async def rates(
    model_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[RateInfo]:
    return await container.models.rates.list(session, principal, model_id)


@router.post('/{model_id}/rate-cards', status_code=201)
async def create_rate(
    model_id: UUID,
    body: RateCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RateInfo:
    async with session.begin():
        return await container.models.rates.create(session, principal, model_id, body)


@router.post('/{model_id}/{action}')
async def model_action(
    model_id: UUID,
    action: Literal['enable', 'disable'],
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> ConsoleModelInfo:
    fields: dict[str, object] = {'enabled': action == 'enable'}
    if action == 'enable':
        return console_model(
            await container.model_configuration.save(principal, fields, model_id),
            container.settings.chat_context_input_tokens,
        )
    async with session.begin():
        return console_model(
            await container.models.catalog.update(session, principal, model_id, fields),
            container.settings.chat_context_input_tokens,
        )
