from typing import TYPE_CHECKING, cast
from uuid import uuid4

from zhiheng.modules.chat.orm import ChatMessage
from zhiheng.modules.chat.service import ChatService
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class HistorySession:
    def __init__(self, rows: list[ChatMessage]) -> None:
        self.rows = rows

    async def scalars(self, query: object) -> list[ChatMessage]:
        return list(reversed(self.rows))


async def test_context_uses_one_latest_complete_reply_per_question() -> None:
    run_id, question_id = uuid4(), uuid4()
    rows = [
        ChatMessage(
            id=question_id, run_id=run_id, role='user', content='question', attachments=[], attachment_inputs=[]
        ),
        ChatMessage(id=uuid4(), run_id=run_id, role='assistant', content='first', attachments=[], attachment_inputs=[]),
        ChatMessage(
            id=uuid4(), run_id=uuid4(), role='assistant', content='second', attachments=[], attachment_inputs=[]
        ),
    ]
    for row in rows:
        row.is_complete = True
    # Legacy assistant messages have no explicit reply link; regenerated messages do.
    rows[-1].reply_to_message_id = question_id
    rows[-1].generation_index = 2
    turns = await ChatService().context_turns(
        cast('AsyncSession', HistorySession(rows)), Principal(uuid4(), 'owner', False), uuid4()
    )
    assert len(turns) == 1
    assert turns[0].question_id == question_id and turns[0].reply_id == rows[-1].id
    assert turns[0].generation_index == 2
    assert [(message.role, message.content) for message in (turns[0].question, turns[0].reply)] == [
        ('user', 'question'),
        ('assistant', 'second'),
    ]
