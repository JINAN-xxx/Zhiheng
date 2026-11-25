"""Validate the versioned JSON boundary of durable knowledge preparation."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from zhiheng.modules.agents.public import AgentConfig, PreviewRequest
from zhiheng.modules.files.public import PreparedAttachment
from zhiheng.modules.models.public import ModelBinding, ModelRequest
from zhiheng.shared.errors import AppError

PAYLOAD_VERSION = 1
_VERSION_KEY = 'payload_version'
_Version = Annotated[int, Field(strict=True, ge=1)]
_Timeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class _RetrievalInput(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=8, strict=True, ge=1, le=15)


class _StandaloneInput(_RetrievalInput):
    client_request_id: UUID | None = None
    similarity_threshold: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)


class _AssistantTurnInput(_RetrievalInput):
    message: str
    runtime_request_id: UUID
    runtime_fingerprint: str
    attachment_inputs: list[PreparedAttachment] = Field(default_factory=list)


class _AssistantRequest(_RetrievalInput):
    body: PreviewRequest
    runtime_request_id: UUID
    runtime_fingerprint: str


class _WorkTurnBody(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    agent_id: UUID
    client_request_id: UUID
    message: str
    expected_version: _Version


class _WorkTurnInput(_RetrievalInput):
    body: _WorkTurnBody
    admission_body: _WorkTurnBody


class _Dependencies(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    payload_version: Literal[1]
    bases: list[dict[str, object]] = Field(min_length=1, max_length=20)
    rerank_binding: ModelBinding
    rerank_timeout_seconds: _Timeout
    embedding_timeout_seconds: _Timeout


class _AssistantTurnDependencies(_Dependencies):
    agent_id: UUID
    configuration: str
    main_request: ModelRequest
    plaza_version: _Version | None = None


class _AssistantRequestDependencies(_Dependencies):
    agent_version: _Version
    config: AgentConfig
    skills_hash: str
    main_request: ModelRequest


class _WorkDependencies(_Dependencies):
    task_version: _Version
    member_config: dict[str, object]
    member_skills: list[dict[str, object]]
    agent_version: _Version
    agent_config: AgentConfig | None
    context_hash: str
    main_request: ModelRequest


_CONTRACTS: dict[str, tuple[type[_RetrievalInput], type[_Dependencies]]] = {
    'knowledge_query': (_StandaloneInput, _Dependencies),
    'agent_chat': (_AssistantTurnInput, _AssistantTurnDependencies),
    'agent_preview': (_AssistantRequest, _AssistantRequestDependencies),
    'agent_invocation': (_AssistantRequest, _AssistantRequestDependencies),
    'work_turn': (_WorkTurnInput, _WorkDependencies),
}


def validate_pending_payload(resource_type: str, request: dict[str, object], dependencies: dict[str, object]) -> None:
    if _VERSION_KEY not in dependencies:
        # Old rows retain their original shape. Historical chat preparations had
        # no active producer and remain on the legacy reader.
        if resource_type in _CONTRACTS or resource_type == 'chat':
            return
    else:
        version = dependencies[_VERSION_KEY]
        if type(version) is not int or version != PAYLOAD_VERSION:
            raise AppError('knowledge_payload_version', '知识准备记录版本不受支持', 409)
        contract = _CONTRACTS.get(resource_type)
        if contract is not None:
            request_type, dependency_type = contract
            try:
                request_type.model_validate(request)
                # Mutable stage results are validated where they are used, not on
                # every claim or model authorization throughout the workflow.
                dependency_type.model_validate(
                    {
                        key: value
                        for key, value in dependencies.items()
                        if key not in {'retrieval_candidates', 'retrieval_diagnostics'}
                    }
                )
            except ValidationError:
                raise AppError('knowledge_payload_invalid', '知识准备记录的请求或依赖无效', 409) from None
            return
    raise AppError('knowledge_payload_origin', '知识准备记录来源不受支持', 409)


def version_dependencies(
    resource_type: str, request: dict[str, object], dependencies: dict[str, object]
) -> dict[str, object]:
    versioned = {**dependencies, _VERSION_KEY: PAYLOAD_VERSION}
    validate_pending_payload(resource_type, request, versioned)
    return versioned
