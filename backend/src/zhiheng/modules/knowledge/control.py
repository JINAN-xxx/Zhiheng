"""Cancellation, deadlines and fenced leases shared by owned index operations."""

import threading
from datetime import datetime
from time import monotonic

from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


class VectorIOControl:
    def __init__(
        self, *, timeout_seconds: float = 60, deadline: datetime | None = None, lease_until: datetime | None = None
    ) -> None:
        now = monotonic()
        self._deadline = now + (max(0, (deadline - utcnow()).total_seconds()) if deadline else timeout_seconds)
        self._method_deadline = self._deadline
        self._lease = now + max(0, (lease_until - utcnow()).total_seconds()) if lease_until else None
        self._lock, self._cancelled = threading.Lock(), threading.Event()
        self._parent: VectorIOControl | None = None

    def fork(self) -> 'VectorIOControl':
        child = VectorIOControl(timeout_seconds=max(0, self._deadline - monotonic()))
        child._deadline = self._deadline
        child._method_deadline = self._deadline
        child._cancelled = self._cancelled
        child._parent = self
        return child

    def renew(self, lease_until: datetime) -> None:
        """Only called after the fenced SQL renewal transaction commits."""
        with self._lock:
            if self._lease is not None and monotonic() >= self._lease:
                return
            self._lease = monotonic() + max(0, (lease_until - utcnow()).total_seconds())

    def cancel(self) -> None:
        self._cancelled.set()

    def method(self, seconds: float) -> None:
        self.check_operation()
        self._method_deadline = min(self._deadline, monotonic() + seconds)

    def check_operation(self) -> None:
        if self._parent is not None:
            self._parent.check_operation()
        if self._cancelled.is_set():
            raise AppError('index_operation_cancelled', '索引操作已取消', 409)
        with self._lock:
            if self._lease is not None and monotonic() >= self._lease:
                raise AppError('execution_lost', '知识执行权已失效', 409)
        if monotonic() >= self._deadline:
            raise AppError('knowledge_timeout', '知识索引IO超过执行期限', 503)

    def check(self) -> None:
        self.check_operation()
        if monotonic() >= self._method_deadline:
            raise AppError('knowledge_timeout', '知识索引IO超过执行期限', 503)

    def remaining_operation(self) -> float:
        self.check_operation()
        return max(0.001, self._deadline - monotonic())

    def remaining(self) -> float:
        self.check()
        return max(0.001, min(self._deadline, self._method_deadline) - monotonic())
