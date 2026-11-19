"""One generation deadline with independent first-response and idle clocks."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import monotonic
from typing import Literal

from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

TimeoutPhase = Literal['connection', 'first_response', 'idle', 'finalize', 'total']
MODEL_TIMEOUT_CODES = frozenset(
    {
        'execution_deadline_exceeded',
        'model_connection_timeout',
        'model_first_response_timeout',
        'model_idle_timeout',
        'model_finalize_timeout',
        'model_deadline_exceeded',
    }
)


class ModelExecutionTimeout(AppError):
    def __init__(self, phase: TimeoutPhase) -> None:
        self.phase = phase
        code, message = {
            'connection': ('model_connection_timeout', '连接模型服务超时，请检查服务地址与网络'),
            'first_response': ('model_first_response_timeout', '等待模型首个有效输出超时，原调用用量待核对'),
            'idle': ('model_idle_timeout', '模型连续一段时间未返回新内容，已保留收到的内容，原调用用量待核对'),
            'finalize': ('model_finalize_timeout', '模型内容已返回，但结果收尾超时，已保留内容与收到的用量'),
            'total': ('model_deadline_exceeded', '已达到本轮生成时限，已保留收到的内容，可继续任务或重新生成'),
        }[phase]
        super().__init__(code, message, 504)


@dataclass
class ModelExecutionClock:
    first_response_seconds: float
    idle_seconds: float
    total_seconds: float
    execution_deadline: datetime | None = None
    started_at: float = field(default_factory=monotonic)
    provider_started_at: float | None = None
    last_activity_at: float | None = None
    first_activity_at: float | None = None
    deadline_at: datetime = field(init=False)
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        now = utcnow()
        self.deadline_at = (
            min(now + timedelta(seconds=self.total_seconds), self.execution_deadline)
            if self.execution_deadline
            else now + timedelta(seconds=self.total_seconds)
        )
        self.deadline = self.started_at + max(0, (self.deadline_at - now).total_seconds())

    def activity(self) -> None:
        now = monotonic()
        if self.first_activity_at is None:
            self.first_activity_at = now
        self.last_activity_at = now

    def check(self, *, completed: bool = False) -> None:
        now = monotonic()
        if now >= self.deadline:
            raise ModelExecutionTimeout('total')
        if self.provider_started_at is None:
            return
        if self.last_activity_at is None:
            if now - self.provider_started_at >= self.first_response_seconds:
                raise ModelExecutionTimeout('first_response')
        elif now - self.last_activity_at >= self.idle_seconds:
            raise ModelExecutionTimeout('finalize' if completed else 'idle')


def remaining_seconds(deadline: datetime | None, default: float) -> float:
    return max(0.001, min(default, (deadline - utcnow()).total_seconds())) if deadline else default
