"""Count bytes handed to the ASGI transport before recording a private download audit."""

from http import HTTPStatus
from uuid import UUID

from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from zhiheng.bootstrap.container import Container
from zhiheng.shared.principal import Principal


class AuditedFileResponse(Response):
    def __init__(
        self,
        content: bytes,
        *,
        status_code: int,
        headers: dict[str, str],
        container: Container,
        principal: Principal,
        file_id: UUID,
        range_header: str | None,
    ) -> None:
        super().__init__(content, status_code=status_code, headers=headers, media_type='application/octet-stream')
        self._container, self._principal, self._file_id, self._range = container, principal, file_id, range_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        sent = 0

        async def counted(message: Message) -> None:
            nonlocal sent
            await send(message)
            if message['type'] == 'http.response.body':
                sent += len(message.get('body', b''))

        try:
            await super().__call__(scope, receive, counted)
        finally:
            async with self._container.database.sessions.begin() as session:
                await self._container.files.record_access(
                    session,
                    self._principal,
                    self._file_id,
                    outcome='allowed' if self.status_code in {200, 206} else 'denied',
                    bytes_sent=sent,
                    range_header=self._range,
                    error_code='invalid_range'
                    if self.status_code == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
                    else None,
                )
