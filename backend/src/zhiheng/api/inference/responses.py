"""Successful inference wire contracts shared by OpenAPI and protocol checks."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    prompt_tokens: int | None = Field(description='已确认的输入 Token 数；供应商未提供时为 null')
    completion_tokens: int | None = Field(description='已确认的输出 Token 数；供应商未提供时为 null')
    total_tokens: int | None = Field(description='输入与输出合计；任一未确认时为 null')


class CompletionMessage(BaseModel):
    role: Literal['assistant']
    content: str = Field(description='模型回复正文')
    reasoning_content: str | None = Field(default=None, description='供应商提供时返回的推理文本')


class CompletionChoice(BaseModel):
    index: int
    message: CompletionMessage
    finish_reason: str = Field(description='结束原因，例如 stop 或 length')


class CompletionResponse(BaseModel):
    id: str = Field(description='平台运行对应的 chatcmpl 标识')
    object: Literal['chat.completion']
    created: int = Field(description='受理时间，Unix 秒')
    model: str = Field(description='平台模型 UUID')
    choices: list[CompletionChoice] = Field(description='回复候选；包含序号、回复正文和结束原因')
    usage: TokenUsage = Field(description='供应商确认的 Token 用量；未知字段为 null')


class AgentCompletionResponse(CompletionResponse):
    conversation_id: UUID = Field(description='续聊时提交的会话标识')


class EmbeddingItem(BaseModel):
    object: Literal['embedding']
    index: int = Field(description='对应输入文本的序号')
    embedding: list[float] = Field(description='该输入的向量')


class EmbeddingResponse(BaseModel):
    object: Literal['list']
    model: str = Field(description='平台向量模型 UUID')
    data: list[EmbeddingItem] = Field(description='按输入序号返回的向量列表')
    usage: TokenUsage


class RerankDocument(BaseModel):
    text: str


class RerankItem(BaseModel):
    index: int = Field(description='原候选文档的序号')
    relevance_score: float = Field(description='相关性分数')
    document: RerankDocument | None = Field(default=None, description='return_documents=true 时返回')


class RerankBilledUnits(BaseModel):
    search_units: int | None = None


class RerankTokens(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class RerankMeta(BaseModel):
    billed_units: RerankBilledUnits
    tokens: RerankTokens


class RerankResponse(BaseModel):
    id: str = Field(description='平台运行 UUID')
    model: str = Field(description='平台重排模型 UUID')
    results: list[RerankItem] = Field(description='按相关性排序的候选结果')
    meta: RerankMeta = Field(description='供应商确认的检索单元与 Token 用量')
