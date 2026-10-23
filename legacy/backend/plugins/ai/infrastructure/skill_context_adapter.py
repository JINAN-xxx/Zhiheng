from __future__ import annotations

from typing import TYPE_CHECKING, Any

from plugins.ai.service.platform_chat_service import PlatformChatError, ResolvedChatSkills

if TYPE_CHECKING:
    from collections.abc import Callable

_SKILL_ERRORS: dict[str, Callable[[], PlatformChatError]] = {
    'SKILL_NOT_FOUND': PlatformChatError.skill_not_found,
    'SKILL_DISABLED': PlatformChatError.skill_disabled,
    'SKILL_SELECTION_LIMIT': PlatformChatError.skill_selection_limit,
    'SKILL_CONTENT_TOO_LARGE': PlatformChatError.skill_content_too_large,
}


class SkillContextAdapter:
    """通过独立只读会话调用可选技能插件，隔离 Platform Chat 写事务。"""

    def __init__(self, session_factory: Any, provider: object | None) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def resolve(self, user_id: int, slugs: list[str]) -> ResolvedChatSkills:
        if not slugs:
            return ResolvedChatSkills(system_prompt='', snapshots=())
        if self._provider is None:
            raise PlatformChatError.skill_unavailable()
        try:
            async with self._session_factory() as db:
                result = await self._provider.resolve(db, user_id, slugs)
        except Exception as error:
            factory = _SKILL_ERRORS.get(getattr(error, 'code', ''))
            raise (factory() if factory is not None else PlatformChatError.skill_unavailable()) from None
        system_prompt = getattr(result, 'system_prompt', None)
        raw_snapshots = getattr(result, 'snapshots', None)
        if not isinstance(system_prompt, str) or not isinstance(raw_snapshots, (list, tuple)):
            raise PlatformChatError.skill_unavailable()
        snapshots: list[dict[str, str]] = []
        for item in raw_snapshots:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get('slug'), str)
                or not isinstance(item.get('name'), str)
            ):
                raise PlatformChatError.skill_unavailable()
            snapshots.append({'slug': item['slug'], 'name': item['name']})
        return ResolvedChatSkills(system_prompt=system_prompt, snapshots=tuple(snapshots))
