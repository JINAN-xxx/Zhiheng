"""Safe categories for provider exceptions; never retain their text or payloads."""

import re
from http import HTTPStatus
from typing import cast

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError

from zhiheng.modules.models.contracts import (
    ProviderError,
    ProviderExceptionType,
    ProviderFailureReason,
    ProviderRateLimitMetric,
)

_EXCEPTION_CATEGORIES: tuple[tuple[type[Exception], ProviderFailureReason, ProviderExceptionType], ...] = (
    (APITimeoutError, 'timeout', 'APITimeoutError'),
    (APIConnectionError, 'transport_error', 'APIConnectionError'),
    (APIError, 'api_error', 'APIError'),
    (httpx.TimeoutException, 'timeout', 'TimeoutException'),
    (httpx.HTTPError, 'transport_error', 'HTTPError'),
    (TimeoutError, 'timeout', 'TimeoutError'),
    (ValueError, 'invalid_response', 'ValueError'),
    (KeyError, 'invalid_response', 'KeyError'),
)
_RATE_LIMIT_DETAIL = re.compile(r'(?:^|\s)Details:(RPM|RPH|RPD|TPM|TPD|IPM|IPD) limit reached\.?\s*\Z')


def _insufficient_balance(error: Exception, body: object) -> bool:
    """Zhipu documents HTTP 429/code 1113 as an exhausted upstream account.

    Bind vendor codes to the actual request host. A custom endpoint can assign
    the same numeric code a different meaning; arbitrary messages are ignored.
    """
    if not isinstance(error, (APIStatusError, httpx.HTTPStatusError)) or not isinstance(body, dict):
        return False
    try:
        if error.request.url.host != 'open.bigmodel.cn':
            return False
    except (AttributeError, RuntimeError):
        return False
    codes = [body['code']] if 'code' in body else []
    nested = body.get('error')
    if isinstance(nested, dict) and 'code' in nested:
        codes.append(nested['code'])
    return bool(codes) and all(type(code) in (str, int) and str(code) == '1113' for code in codes)


def _rate_limit_metric(body: object) -> ProviderRateLimitMetric:
    if not isinstance(body, dict):
        return 'unknown'
    messages = [body.get('message')]
    nested = body.get('error')
    if isinstance(nested, dict):
        messages.append(nested.get('message'))
    metrics: set[ProviderRateLimitMetric] = set()
    for message in messages:
        if not isinstance(message, str):
            continue
        # Only a documented, unambiguous suffix becomes a fixed category.
        # The original message and any other response fields never leave here.
        if message.count('Details:') > 1:
            return 'unknown'
        match = _RATE_LIMIT_DETAIL.search(message)
        if match is not None:
            metrics.add(cast('ProviderRateLimitMetric', match[1].lower()))
    return next(iter(metrics)) if len(metrics) == 1 else 'unknown'


def provider_error(error: Exception) -> ProviderError:
    status: int | None = None
    error_type: ProviderExceptionType | None = None
    body: object = None
    if isinstance(error, APIStatusError):
        status, error_type = error.status_code, 'APIStatusError'
        body = error.body
    elif isinstance(error, httpx.HTTPStatusError):
        status, error_type = error.response.status_code, 'HTTPStatusError'
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            try:
                body = error.response.json()
            except (ValueError, RecursionError, httpx.ResponseNotRead):
                pass
    if error_type is not None:
        balance_exhausted = status == HTTPStatus.TOO_MANY_REQUESTS and _insufficient_balance(error, body)
        return ProviderError(
            definitive_rejection=status in {400, 401, 403, 404, 422, 429},
            reason='insufficient_balance' if balance_exhausted else 'http_error',
            upstream_status=status,
            upstream_error_type=error_type,
            rate_limit_metric=_rate_limit_metric(body)
            if status == HTTPStatus.TOO_MANY_REQUESTS and not balance_exhausted
            else 'unknown',
        )
    for exception_class, reason, error_type in _EXCEPTION_CATEGORIES:
        if isinstance(error, exception_class):
            return ProviderError(reason=reason, upstream_error_type=error_type)
    return ProviderError()
