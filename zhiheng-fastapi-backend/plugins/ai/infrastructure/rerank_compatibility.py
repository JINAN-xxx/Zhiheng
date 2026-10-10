"""Keep provider identity separate from SiliconFlow's rerank wire protocol."""

from collections.abc import Mapping

SILICONFLOW_BASE = 'https://api.siliconflow.cn/v1'
SILICONFLOW_RERANK = f'{SILICONFLOW_BASE}/rerank'
_COMPATIBLE_PROVIDERS = frozenset({'openai', 'custom_openai'})


def prepare_rerank_payload(payload: dict[str, object], *, model_type: str | None = None) -> dict[str, object]:
    params = payload.get('litellm_params')
    info = payload.get('model_info', {})
    if not isinstance(params, dict) or not isinstance(info, dict):
        return payload
    mode = model_type or payload.get('mode') or info.get('mode')
    base = params.get('api_base')
    provider = params.get('custom_llm_provider')
    if mode != 'rerank' or not isinstance(provider, str) or provider not in _COMPATIBLE_PROVIDERS:
        return payload
    if not isinstance(base, str) or base.rstrip('/') != SILICONFLOW_BASE:
        return payload
    return {
        **payload,
        'model_info': {**info, 'mode': 'rerank', 'zhiheng_provider': provider},
        'litellm_params': {
            **params,
            'custom_llm_provider': 'cohere',
            'api_base': SILICONFLOW_RERANK,
            # SiliconFlow returns document:null when omitted; LiteLLM's Cohere
            # response schema expects a dictionary. Public results are rebuilt
            # from the caller's documents according to returnDocuments.
            'return_documents': True,
        },
    }


def restore_connection_parameters(params: Mapping, info: Mapping) -> Mapping:
    """Expose the saved connection identity instead of the internal wire adapter."""
    base = params.get('api_base')
    provider = info.get('zhiheng_provider')
    if (
        params.get('custom_llm_provider') == 'cohere'
        and isinstance(base, str)
        and base.rstrip('/') == SILICONFLOW_RERANK
        and info.get('mode') == 'rerank'
        and isinstance(provider, str)
        and provider in _COMPATIBLE_PROVIDERS
    ):
        return {**params, 'api_base': SILICONFLOW_BASE, 'custom_llm_provider': provider}
    return params
