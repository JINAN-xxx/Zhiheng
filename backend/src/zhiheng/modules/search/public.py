"""Shared web-search capability for authorized platform applications."""

from zhiheng.modules.search.contracts import (
    Freshness,
    WebSearch,
    WebSearchError,
    WebSearchRequest,
    WebSearchResult,
    WebSearchSource,
)

__all__ = [
    'Freshness',
    'WebSearch',
    'WebSearchError',
    'WebSearchRequest',
    'WebSearchResult',
    'WebSearchSource',
]
