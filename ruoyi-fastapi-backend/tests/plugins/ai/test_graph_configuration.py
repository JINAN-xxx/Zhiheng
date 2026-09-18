from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from plugins.ai.knowledge import graph_config, models

MODEL_ID = 'mdl_423e4567e89b42d3a456426614174444'


def test_graph_configuration_has_bounded_defaults_and_rejects_arbitrary_provider_body() -> None:
    schema = getattr(models, 'GraphConfiguration', None)
    assert schema is not None, '图谱需要独立且严格的配置模型'
    config = schema(llm_model_id=MODEL_ID)
    assert config.reasoning_strategy == 'auto'
    assert config.model_dump() == {
        'llm_model_id': MODEL_ID,
        'reasoning_strategy': 'auto',
        'timeout_seconds': 240,
        'max_output_tokens': 16384,
        'concurrency': 2,
    }
    for invalid in (
        {'concurrency': 0},
        {'timeout_seconds': 601},
        {'max_output_tokens': 0},
        {'reasoning_strategy': 'maximum'},
        {'extra_body': {'api_key': 'secret'}},
    ):
        with pytest.raises(ValidationError):
            schema(llm_model_id=MODEL_ID, **invalid)


def test_graph_model_policy_uses_catalog_code_and_leaves_unknown_models_at_defaults() -> None:
    config = models.GraphConfiguration(llm_model_id=MODEL_ID)
    glm = SimpleNamespace(model_code='zai-org-glm-5.3', engine_model_snapshot='opaque-runtime-alias')
    deepseek = SimpleNamespace(model_code='deepseek-v4-flash', engine_model_snapshot='other-alias')
    unknown = SimpleNamespace(model_code='unknown', engine_model_snapshot='unknown-alias')
    assert graph_config.graph_parameters(config, glm) == {'max_tokens': 16384, 'reasoning_effort': 'low'}
    assert graph_config.graph_parameters(config, deepseek) == {'max_tokens': 16384, 'thinking_mode': 'disabled'}
    assert graph_config.graph_parameters(config, unknown) == {'max_tokens': 16384}
    assert graph_config.graph_parameters(config.model_copy(update={'reasoning_strategy': 'default'}), glm) == {
        'max_tokens': 16384
    }


def test_graph_fingerprint_changes_with_binding_parameters_and_chunking() -> None:
    config = models.GraphConfiguration(llm_model_id=MODEL_ID)
    resolved = SimpleNamespace(
        model_code='glm-5.3',
        platform_model_id=MODEL_ID,
        binding_id='binding-a',
        engine_model_snapshot='alias-a',
        default_parameters={},
    )
    database = {'chunk_size': 1000, 'chunk_overlap': 200}
    original = graph_config.graph_fingerprint(config, resolved, database)
    assert original == graph_config.graph_fingerprint(config, resolved, dict(database))
    assert original != graph_config.graph_fingerprint(
        config.model_copy(update={'reasoning_strategy': 'default'}), resolved, database
    )
    assert original != graph_config.graph_fingerprint(config, resolved, dict(database, chunk_size=500))
    resolved.binding_id = 'binding-b'
    assert original != graph_config.graph_fingerprint(config, resolved, database)
