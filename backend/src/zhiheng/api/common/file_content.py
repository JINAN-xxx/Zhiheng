"""Shared native download headers, single Range handling and transport audit."""

from urllib.parse import quote

from fastapi import Request, Response

from zhiheng.api.common.resource_protocol import resource_error
from zhiheng.api.file_download import AuditedFileResponse
from zhiheng.bootstrap.container import Container
from zhiheng.modules.files.public import FileInfo
from zhiheng.shared.principal import Principal

MAX_RANGE_HEADER_LENGTH = 128


async def file_content_response(
    request: Request,
    container: Container,
    principal: Principal,
    info: FileInfo,
    content: bytes,
    *,
    resource: bool = False,
) -> Response:
    size = len(content)
    start, end, status = 0, size - 1, 200
    headers = {
        'ETag': f'"file-{info.version}"' if resource else f'"{info.sha256}"',
        'Accept-Ranges': 'bytes',
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox",
        'Cache-Control': 'private, no-store',
        'Content-Disposition': f"attachment; filename=download; filename*=UTF-8''{quote(info.original_filename, safe='')}",
    }
    ranges = request.headers.getlist('range')
    if ranges:
        try:
            if len(ranges) != 1 or len(ranges[0]) > MAX_RANGE_HEADER_LENGTH or not ranges[0].startswith('bytes='):
                raise ValueError
            first, last = ranges[0][6:].split('-')
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                suffix = int(last)
                if suffix <= 0:
                    raise ValueError
                start = max(0, size - suffix)
            if start < 0 or end < start or start >= size:
                raise ValueError
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            status = 206
        except ValueError:
            headers['Content-Range'] = f'bytes */{size}'
            if resource:
                response = resource_error(request, 'invalid_range', '请求Range无效', 416, headers=headers)
                # Reuse the native audit response so actual transfer and denial remain recorded.
                return AuditedFileResponse(
                    bytes(response.body),
                    status_code=416,
                    headers=dict(response.headers),
                    container=container,
                    principal=principal,
                    file_id=info.id,
                    range_header=request.headers.get('range'),
                )
            return AuditedFileResponse(
                b'',
                status_code=416,
                headers=headers,
                container=container,
                principal=principal,
                file_id=info.id,
                range_header=request.headers.get('range'),
            )
    payload = content[start : end + 1]
    headers['Content-Length'] = str(len(payload))
    return AuditedFileResponse(
        payload,
        status_code=status,
        headers=headers,
        container=container,
        principal=principal,
        file_id=info.id,
        range_header=request.headers.get('range'),
    )
