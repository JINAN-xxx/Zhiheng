"""Construct the existing provider adapters from a frozen model binding."""

from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.adapters.structured import StructuredProvider
from zhiheng.modules.models.contracts import ModelBinding, ModelProvider


def create_provider(*, base_url: str, api_key: str, binding: ModelBinding, timeout_seconds: float) -> ModelProvider:
    if binding.model_type != 'chat':
        return StructuredProvider(
            base_url=base_url,
            api_key=api_key,
            model=binding.upstream_model,
            timeout_seconds=timeout_seconds,
            model_type=binding.model_type,
            options=binding.options.model_dump(mode='json', exclude_none=True),
        )
    return OpenAIChatProvider(
        base_url=base_url,
        api_key=api_key,
        model=binding.upstream_model,
        timeout_seconds=timeout_seconds,
        max_output_tokens=binding.output_limit_tokens,
        options=binding.options.model_dump(mode='json', exclude_none=True),
    )
