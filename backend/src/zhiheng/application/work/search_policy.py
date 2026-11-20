"""Per-run search limits and definitive provider failures; no cross-turn lockout."""

from dataclasses import dataclass
from uuid import UUID

from zhiheng.modules.work.public import WorkStepInfo
from zhiheng.shared.errors import AppError

SEARCH_STOP_CODES = frozenset(
    {
        'web_search_quota_exhausted',
        'web_search_rejected',
        'web_search_unavailable',
        'web_search_invalid_response',
    }
)


@dataclass
class SearchPolicy:
    limit: int = 6
    calls: int = 0
    failure: tuple[str, str] | None = None

    @property
    def available(self) -> bool:
        return self.failure is None and self.calls < self.limit

    def begin(self) -> None:
        if self.failure is not None:
            raise AppError(self.failure[0], self.failure[1], 503)
        if self.calls >= self.limit:
            raise AppError('web_search_turn_limit', '本轮搜索预算已用完，请根据现有来源答复并说明尚未确认的信息', 409)
        self.calls += 1

    def failed(self, error: AppError) -> None:
        if error.code in SEARCH_STOP_CODES:
            self.failure = (error.code, error.message)

    def metadata(self) -> dict[str, object]:
        return {
            'remaining_attempts': max(0, self.limit - self.calls),
            'available': self.available,
            'retryable': self.available,
            'attempts': self.calls,
            'notice': ''
            if self.available
            else '本轮不再搜索。根据已有来源回答或说明未查到；禁网沙盒不能替代联网查询。新一轮请求可以重新尝试。',
        }

    def cached(self, result: dict[str, object]) -> None:
        error = result.get('error')
        if isinstance(error, dict) and error.get('code') in SEARCH_STOP_CODES:
            self.failure = (str(error['code']), str(error.get('message', '搜索服务当前不可用')))

    def restore(self, steps: list[WorkStepInfo], run_id: UUID) -> None:
        for step in steps:
            if step.run_id != run_id or step.name != 'web_search' or step.status == 'pending':
                continue
            result = step.result or {}
            policy = result.get('search_policy')
            attempts = policy.get('attempts') if isinstance(policy, dict) else None
            self.calls = max(self.calls, attempts) if type(attempts) is int else self.calls + 1
            self.cached(result)
