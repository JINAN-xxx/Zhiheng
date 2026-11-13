from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from zhiheng.modules.knowledge.retrieval import RetrievalStore


async def test_revalidate_locks_all_libraries_before_any_source() -> None:
    events = []
    bases = []
    documents = {}
    for base_id in [UUID(int=2), UUID(int=1)]:
        source = SimpleNamespace(file_id=uuid4())
        document_id = uuid4()
        bases.append(
            SimpleNamespace(
                base_id=base_id,
                version=1,
                acl_version=1,
                spec=SimpleNamespace(revision=uuid4()),
                sources=[SimpleNamespace(document_id=document_id, document_version=1, source=source)],
            )
        )
        documents[document_id] = SimpleNamespace(version=1, status='ready', source=source)
    by_id = {base.base_id: base for base in bases}

    class Knowledge:
        async def require(self, session: Any, principal: Any, base_id: UUID, *, lock: bool) -> Any:
            assert lock
            events.append(('kb', base_id))
            frozen = by_id[base_id]
            return SimpleNamespace(active_revision=frozen.spec.revision, version=1, acl_version=1)

        async def document(self, session: Any, base_id: UUID, document_id: UUID) -> Any:
            return documents[document_id]

        async def document_source_snapshot(self, session: Any, principal: Any, document: Any, *, lock: bool) -> Any:
            assert lock
            events.append(('file', document.source.file_id))
            return document.source

    await RetrievalStore(Knowledge()).revalidate(None, None, bases, lock=True)
    ordered = sorted(bases, key=lambda base: base.base_id)
    assert events == [('kb', base.base_id) for base in ordered] + [
        ('file', base.sources[0].source.file_id) for base in ordered
    ]
