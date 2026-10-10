"""Use verified lightweight provider options for connection probes only."""


def prepare_connection_probe(payload: dict[str, object]) -> dict[str, object]:
    params = payload.get('litellm_params')
    if payload.get('mode') != 'chat' or not isinstance(params, dict):
        return payload
    api_base = params.get('api_base')
    if (
        not isinstance(api_base, str)
        or api_base.rstrip('/') != 'https://api.siliconflow.cn/v1'
        or params.get('model') != 'Qwen/Qwen3.8-27B'
        or params.get('custom_llm_provider') not in {'openai', 'custom_openai'}
    ):
        return payload
    extra_body = params.get('extra_body', {})
    if not isinstance(extra_body, dict):
        return payload
    # SiliconFlow's max_tokens excludes reasoning tokens. This model supports
    # disabling thinking, so a connectivity probe need not spend its deadline
    # producing an internal reasoning trace. Do not change saved model options.
    return {
        **payload,
        'litellm_params': {**params, 'extra_body': {**extra_body, 'enable_thinking': False}},
    }
