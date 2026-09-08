from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_HISTORY_BYTES = 65536

class AgentMessageModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    role: Literal['user', 'assistant']
    content: Annotated[str, StringConstraints(min_length=1, max_length=MAX_HISTORY_BYTES)]


class AgentInvocationRequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    messages: list[AgentMessageModel] = Field(min_length=1, max_length=96)
    stream: bool = False

    @model_validator(mode='after')
    def validate_history(self) -> 'AgentInvocationRequestModel':
        if self.messages[-1].role != 'user':
            raise ValueError('最后一条消息必须是用户问题')
        if any(not message.content.strip() for message in self.messages):
            raise ValueError('消息不能为空')
        if sum(len(message.content.encode('utf-8')) for message in self.messages) > MAX_HISTORY_BYTES:
            raise ValueError('对话消息超过大小限制')
        return self
