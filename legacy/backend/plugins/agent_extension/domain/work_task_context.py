from collections.abc import Sequence

from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage
from plugins.agent_extension.entity.vo.agent_invocation_vo import MAX_HISTORY_BYTES, AgentInvocationRequestModel

MAX_HISTORY_MESSAGES = 96


def build_work_history(
    messages: Sequence[AgentWorkMessage], latest_user: AgentWorkMessage
) -> AgentInvocationRequestModel:
    latest = {'role': 'user', 'content': latest_user.content}
    size = len(latest_user.content.encode('utf-8'))
    if size > MAX_HISTORY_BYTES:
        raise AgentServiceError('WORK_MESSAGE_TOO_LARGE', '消息超出上下文大小限制，请缩短后重试', status_code=422)
    retained = [latest]
    earlier = [message for message in messages if message.sequence < latest_user.sequence]
    for message in reversed(earlier):
        if not message.content.strip():
            continue
        content = message.content
        if message.role == 'assistant':
            content = (
                f'[Agent: {message.agent_name_snapshot}; publishedVersion: {message.published_version}]\n{content}'
            )
        next_size = len(content.encode('utf-8'))
        if len(retained) >= MAX_HISTORY_MESSAGES or size + next_size > MAX_HISTORY_BYTES:
            break
        retained.append({'role': message.role, 'content': content})
        size += next_size
    return AgentInvocationRequestModel(messages=list(reversed(retained)), stream=True)
