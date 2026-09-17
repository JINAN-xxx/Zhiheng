"""Convert untrusted provider errors to fixed, non-sensitive test diagnostics."""

import re
from collections.abc import Mapping

MAX_ERROR_TEXT = 8192
MAX_ERROR_DEPTH = 3
ERROR_FIELDS = ('error', 'message', 'type', 'code', 'status_code', 'status')
MESSAGES = {
    'timeout': '连接测试超时，请检查服务地址和网络后重试',
    'network': '无法连接模型服务，请检查服务地址、网络和证书配置',
    'authentication': '模型服务拒绝了 API Key，请检查凭据是否正确或已失效',
    'permission': '当前凭据没有访问该模型的权限，请检查服务商授权',
    'quota': '模型账户余额或可用额度不足，请检查服务商账户',
    'rate_limit': '模型服务请求频率受限，请稍后重试',
    'model_not_found': '服务商未找到该模型，请核对完整模型名称',
    'invalid_request': '模型服务不接受当前请求，请检查模型类型和参数',
    'upstream_unavailable': '模型服务暂时不可用，请稍后重试',
    'unknown': '连接测试失败，请检查模型类型、名称和连接配置',
}
PATTERNS = (
    ('timeout', r'time.?out|timed out'),
    ('network', r'apiconnectionerror|connecterror|connection error|connection refused|certificate|name resolution|dns'),
    ('quota', r'insufficient.{0,20}(balance|quota|credit)|payment required|\b402\b|余额不足|额度不足'),
    ('rate_limit', r'ratelimit|rate.limit|too many requests|\b429\b'),
    ('authentication', r'authenticationerror|unauthorized|invalid.{0,10}api.key|incorrect.api.key|\b401\b'),
    ('permission', r'permissiondenied|forbidden|\b403\b'),
    ('model_not_found', r'notfounderror|model.{0,40}(not.found|does.not.exist|not.exist)|\b404\b'),
    ('invalid_request', r'badrequesterror|invalid.request|unsupported.parameter|\b400\b|\b422\b'),
    ('upstream_unavailable', r'service.unavailable|internal.server.error|\b50[0234]\b'),
)


def classify_connection_failure(payload: Mapping[str, object]) -> tuple[str, str]:
    result = payload.get('result')
    error = result.get('error') if isinstance(result, Mapping) else result
    if error is None:
        error = payload.get('error')
    text = _error_text(error).lower()
    for reason, pattern in PATTERNS:
        if re.search(pattern, text):
            return reason, MESSAGES[reason]
    return 'unknown', MESSAGES['unknown']


def _error_text(value: object, depth: int = 0) -> str:
    if isinstance(value, str):
        return value[:MAX_ERROR_TEXT]
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if depth >= MAX_ERROR_DEPTH or not isinstance(value, Mapping):
        return ''
    parts = []
    remaining = MAX_ERROR_TEXT
    for field in ERROR_FIELDS:
        part = _error_text(value.get(field), depth + 1)[:remaining]
        parts.append(part)
        remaining -= len(part) + 1
        if remaining <= 0:
            break
    return ' '.join(parts)
