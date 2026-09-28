from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
AI_ROOT = BACKEND_ROOT / 'plugins' / 'ai'


def test_ai_manifest_has_no_agno_or_provider_sdk_dependencies() -> None:
    manifest = yaml.safe_load((AI_ROOT / 'plugin.yaml').read_text(encoding='utf-8'))
    dependencies = manifest['dependencies']['python']
    assert not any(name.split('==', 1)[0] in {'agno', 'anthropic', 'cerebras-cloud-sdk', 'cohere', 'google-genai', 'groq', 'llama-api-client', 'mistralai', 'ollama', 'openai', 'portkey-ai'} for name in dependencies)


def test_active_ai_sources_have_no_legacy_agno_chain() -> None:
    forbidden = ('from agno', 'import agno', 'AiUtil', 'SessionType', 'acancel_run', 'Agent(')
    for source in AI_ROOT.rglob('*.py'):
        if '__pycache__' in source.parts or source.name.startswith('test_'):
            continue
        text = source.read_text(encoding='utf-8')
        assert not any(marker in text for marker in forbidden), source


def test_active_ai_routes_do_not_expose_legacy_model_crud() -> None:
    controller = (AI_ROOT / 'controller' / 'ai_model_controller.py')
    assert not controller.exists()
    assert (AI_ROOT / 'controller' / 'ai_model_catalog_controller.py').exists()
    assert (AI_ROOT / 'controller' / 'ai_model_engine_controller.py').exists()


def test_legacy_model_api_is_not_used_by_active_frontend() -> None:
    frontend = BACKEND_ROOT.parent / 'zhiheng-fastapi-frontend' / 'plugins' / 'ai'
    assert not (frontend / 'api' / 'model.js').exists()
    active = '\n'.join(path.read_text(encoding='utf-8') for path in frontend.rglob('*.vue'))
    assert '/ai/model/list' not in active
    assert '/ai/model/all' not in active
