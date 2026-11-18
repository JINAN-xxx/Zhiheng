from uuid import UUID, uuid4

from zhiheng.modules.knowledge.public import Hit, PreparedKnowledge
from zhiheng.modules.models.public import ModelMessage


def hit(
    *,
    base: UUID | None = None,
    document: UUID | None = None,
    source: UUID | None = None,
    title: str = '接口说明.pdf',
    content: str = '真实资料',
) -> Hit:
    document_id = document or uuid4()
    return Hit(
        knowledge_base_id=base or uuid4(),
        document_id=document_id,
        source_id=source or document_id,
        file_id=uuid4(),
        chunk_id=uuid4(),
        index_revision=uuid4(),
        title=title,
        content=content,
        score=0.8,
    )


def test_numbered_citations_group_interleaved_fragments_without_changing_provenance() -> None:
    first = hit(content='第一个文档的片段一')
    second = hit(content='另一个文档，同名但来源不同')
    third = first.model_copy(update={'chunk_id': uuid4(), 'content': '第一个文档的片段二'})
    prepared = PreparedKnowledge(intent_id=uuid4(), bases=[], hits=[first, second, third])
    original = prepared.model_dump()
    assert prepared.context() == (
        '[1] 接口说明.pdf\n第一个文档的片段一\n'
        '[2] 接口说明.pdf\n另一个文档，同名但来源不同\n'
        '[1] 接口说明.pdf\n第一个文档的片段二'
    )
    assert prepared.model_dump() == original
    assert str(first.source_id) not in prepared.context()


def test_numbered_sources_share_the_same_context_for_assistants_and_other_callers() -> None:
    source = hit()
    prepared = PreparedKnowledge(intent_id=uuid4(), bases=[], hits=[source])
    context = prepared.context()
    assistant = prepared.agent_prefix()
    messages = prepared.apply_context((ModelMessage('user', '查询接口'),))
    assert context in assistant.content and context in messages[-1].content
    assert assistant.role == 'system' and messages[-1].content.endswith('查询接口')
    assert '资料不足' in messages[0].content


def test_document_identity_includes_the_knowledge_base_and_preserves_business_identifiers() -> None:
    first = hit(content='业务编号：' + str(uuid4()))
    second = first.model_copy(update={'knowledge_base_id': uuid4(), 'chunk_id': uuid4()})
    prepared = PreparedKnowledge(intent_id=uuid4(), bases=[], hits=[first, second])
    assert f'[1] {first.title}\n{first.content}' in prepared.context()
    assert f'[2] {second.title}\n{second.content}' in prepared.context()
