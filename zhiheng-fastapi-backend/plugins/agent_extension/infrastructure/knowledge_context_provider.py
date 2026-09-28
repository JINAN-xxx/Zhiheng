from fastapi import FastAPI

from plugins.agent_extension.domain.agent_knowledge import KnowledgeIdentity, KnowledgePassage


class PlatformKnowledgeProvider:
    """将平台知识库的真实文档片段交给智能体已有的引用边界。"""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def retrieve(
        self,
        *,
        identity: KnowledgeIdentity,
        knowledge_base_ids: tuple[str, ...],
        query: str,
    ) -> tuple[KnowledgePassage, ...]:
        runtime = getattr(self._app.state, 'knowledge_runtime', None)
        if runtime is None:
            raise RuntimeError('Knowledge runtime unavailable')
        hits = await runtime.retrieve_context(
            owner_user_id=identity.owner_user_id,
            knowledge_base_ids=knowledge_base_ids,
            query=query,
            top_k=8,
        )
        return tuple(
            KnowledgePassage(
                content=hit.content,
                source_id=hit.source_id,
                title=hit.title,
                knowledge_base_id=hit.knowledge_base_id,
            )
            for hit in hits
        )
