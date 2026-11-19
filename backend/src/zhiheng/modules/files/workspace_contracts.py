"""Read-only office file inspection; values never expose host paths or SDK objects."""

from typing import Protocol

WorkspaceFileView = dict[str, object]


class WorkspaceReader(Protocol):
    def inspect(self, content: bytes, filename: str) -> WorkspaceFileView: ...
    def read_document(
        self,
        content: bytes,
        filename: str,
        start: int = 1,
        limit: int | None = None,
        *,
        offset: int = 0,
        max_characters: int = 131072,
    ) -> WorkspaceFileView: ...
    def read_sheet(
        self, content: bytes, filename: str, sheet: str = '', start_row: int = 1, limit: int = 40
    ) -> WorkspaceFileView: ...
