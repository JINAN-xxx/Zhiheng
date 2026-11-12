"""Select whole historical turns before adding trusted agent context."""

from collections.abc import Sequence
from typing import Protocol, TypeVar


class HistoryMessage(Protocol):
    @property
    def role(self) -> str: ...

    @property
    def content(self) -> str: ...


Message = TypeVar('Message', bound=HistoryMessage)


def select_agent_history(
    messages: Sequence[Message],
    history_rounds: int,
    *,
    max_messages: int | None = None,
    max_bytes: int | None = None,
) -> tuple[Message, ...]:
    """Keep the current user and recent answered groups, including a legacy assistant prefix.

    Each user starts a group. Unanswered historical users do not consume a round.
    Optional Work limits evict entire oldest groups, never the current user.
    Callers provide conversation messages only, with a final current user.
    """
    current = messages[-1]
    groups: list[list[Message]] = []
    group: list[Message] = []
    for message in messages[:-1]:
        if message.role == 'user' and group:
            if any(item.role == 'assistant' for item in group):
                groups.append(group)
            group = []
        group.append(message)
    if any(item.role == 'assistant' for item in group):
        groups.append(group)
    groups = groups[-history_rounds:] if history_rounds else []
    count = sum(len(group) for group in groups) + 1
    size = sum(len(item.content.encode('utf-8')) for group in groups for item in group)
    size += len(current.content.encode('utf-8'))
    while groups and (
        (max_messages is not None and count > max_messages) or (max_bytes is not None and size > max_bytes)
    ):
        removed = groups.pop(0)
        count -= len(removed)
        size -= sum(len(item.content.encode('utf-8')) for item in removed)
    return (*(item for group in groups for item in group), current)
