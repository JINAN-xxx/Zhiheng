import asyncio
import os
import sys

from pydantic import BaseModel, ConfigDict

from zhiheng.modules.knowledge.contracts import MAX_IMAGE_URL_BYTES, MAX_INPUT_BYTES, MAX_TEXT_BYTES, ParseResult
from zhiheng.shared.errors import AppError

MAX_FILENAME_BYTES = 1024
# A legal text byte may require six JSON wire bytes (e.g. U+0001).
MAX_REPLY_BYTES = MAX_TEXT_BYTES * 6 + MAX_IMAGE_URL_BYTES + 16384


class WorkerReply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    result: ParseResult | None = None
    error: str | None = None


class NativeParser:
    """Bounded subprocesses isolate CPU/memory-heavy parsers from API/worker loops."""

    def __init__(self, *, timeout_seconds: float = 15) -> None:
        self._timeout = timeout_seconds
        self._slots = asyncio.Semaphore(2)

    async def parse(self, content: bytes, *, filename: str) -> ParseResult:
        if (
            not content
            or len(content) > MAX_INPUT_BYTES
            or '\n' in filename
            or len(filename.encode()) > MAX_FILENAME_BYTES
        ):
            raise AppError('invalid_parse_input', '解析输入无效', 422)
        async with self._slots:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                '-m',
                'zhiheng.modules.knowledge.adapters.native_worker',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_REPLY_BYTES,
                env={
                    **os.environ,
                    'OPENBLAS_NUM_THREADS': '1',
                    'OMP_NUM_THREADS': '1',
                    'MKL_NUM_THREADS': '1',
                    'NUMEXPR_NUM_THREADS': '1',
                },
            )
            try:
                output, _ = await asyncio.wait_for(
                    process.communicate(filename.encode() + b'\n' + content), self._timeout
                )
            except BaseException as error:
                if process.returncode is None:
                    process.kill()
                await asyncio.shield(process.wait())
                if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
                    raise AppError('parse_timeout', '文档解析超时', 422) from None
                raise
            if process.returncode != 0 or len(output) > MAX_REPLY_BYTES:
                raise AppError('parse_resource_limit', '解析失败或超过资源限制', 422)
            # Worker errors use a separate validated result, never include filesystem/SDK errors.
            try:
                reply = WorkerReply.model_validate_json(output)
            except ValueError:
                raise AppError('parse_failed', '文档解析失败', 422) from None
            if reply.result is None:
                code = reply.error or 'invalid_document'
                messages = {
                    'ocr_required': '文档无可提取文本，需要OCR',
                    'empty_document': '文档没有可提取的内容',
                    'encrypted_document': '文档已加密，需要无密码副本',
                    'unsupported_format': '不支持此文件格式',
                    'document_too_large': '文档展开内容超过限制',
                }
                raise AppError(code, messages.get(code, '文档损坏或格式无效'), 422)
            return reply.result
