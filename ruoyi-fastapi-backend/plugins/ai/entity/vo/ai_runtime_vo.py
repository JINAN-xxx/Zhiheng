from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class AiRuntimeHealthModel(BaseModel):
    """LiteLLM Runtime 健康状态。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    status: Literal['healthy', 'misconfigured', 'unavailable']
    configured: bool
    latency_ms: float
    checked_at: datetime
    message: str


class AiRuntimeModelInfo(BaseModel):
    """Platform 对外展示的 LiteLLM 模型摘要。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    object: str = 'model'
    owned_by: str | None = None
