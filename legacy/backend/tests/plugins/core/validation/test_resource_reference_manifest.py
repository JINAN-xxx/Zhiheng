import pytest

from plugins.core.manifest.schema import BackendManifest


def test_resource_references_default_to_empty() -> None:
    assert BackendManifest(module='plugins.demo').resource_references == {}


@pytest.mark.parametrize(
    'callback_path', ['public.references:has_references', 'plugins.demo.public.references:has_references']
)
def test_resource_reference_declarations_accept_local_callables(callback_path: str) -> None:
    manifest = BackendManifest.model_validate(
        {'module': 'plugins.demo', 'resourceReferences': {'ai.model': callback_path}}
    )

    assert manifest.resource_references == {'ai.model': callback_path}
    assert manifest.model_dump(by_alias=True)['resourceReferences'] == {'ai.model': callback_path}


@pytest.mark.parametrize('callback_path', ['references', 'references:has:extra', 'references:has\n'])
def test_resource_reference_declarations_reject_malformed_callables(callback_path: str) -> None:
    with pytest.raises(ValueError, match='<module_path>:<callable_name>'):
        BackendManifest.model_validate({'module': 'plugins.demo', 'resourceReferences': {'ai.model': callback_path}})


@pytest.mark.parametrize('callback_path', ['plugins.other.references:has', 'plugins.demo_other.references:has'])
def test_resource_reference_declarations_reject_foreign_plugin_modules(callback_path: str) -> None:
    with pytest.raises(ValueError, match='当前插件模块'):
        BackendManifest.model_validate({'module': 'plugins.demo', 'resourceReferences': {'ai.model': callback_path}})
