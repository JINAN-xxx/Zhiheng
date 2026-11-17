from uuid import uuid4

import pytest

from zhiheng.api.console.models import console_model
from zhiheng.modules.models.contracts import ModelConfiguration, ModelInfo
from zhiheng.modules.models.public import chat_input_budget, input_budget_for
from zhiheng.shared.errors import AppError


@pytest.mark.parametrize(
    ('window', 'platform', 'expected', 'source'),
    [
        (None, None, None, 'unknown'),
        (None, 8192, 8192, 'platform'),
        (32768, None, 28416, 'model_window'),
        (32768, 8192, 8192, 'model_window_and_platform'),
    ],
)
def test_default_budget_matches_chat_runtime_and_never_claims_an_unknown_window(
    window: int | None, platform: int | None, expected: int | None, source: str
) -> None:
    budget = chat_input_budget(context_window_tokens=window, max_output_tokens=16384, platform_input_tokens=platform)
    assert budget.limit_tokens == expected and budget.source == source
    assert budget.reserved_output_tokens == 4096
    assert budget.limit_tokens == input_budget_for(
        input_budget=platform, context_window_tokens=window, max_output_tokens=4096
    )
    model = ModelInfo(
        id=uuid4(),
        name='Example',
        connection_id=uuid4(),
        upstream_model='custom',
        max_output_tokens=16384,
        context_window_tokens=window,
        enabled=True,
    )
    assert console_model(model, platform).chat_input_budget == budget


def test_conflicting_capacity_is_rejected_before_any_provider_validation() -> None:
    values = {'name': 'Example', 'connection_id': uuid4(), 'upstream_model': 'custom', 'context_window_tokens': 8192}
    for output in [8192, 7936, 1000000]:
        with pytest.raises(AppError) as error:
            ModelConfiguration(**values, max_output_tokens=output)
        assert error.value.code == 'invalid_model_capacity' and error.value.status_code == 422
    assert ModelConfiguration(**values, max_output_tokens=7935).max_output_tokens == 7935
    assert (
        ModelConfiguration(**{**values, 'context_window_tokens': None}, max_output_tokens=1000000).context_window_tokens
        is None
    )
