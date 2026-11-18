import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_verifier() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / 'deploy/sandbox/runtime/verify_runtime.py'
    spec = importlib.util.spec_from_file_location('workspace_runtime_verifier', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_changed_dependency_declaration_requires_a_matching_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_verifier()
    lock = tmp_path / 'requirements.lock'
    declared = tmp_path / 'requirements.in'
    lock.write_text('example-package==1.0.0\n')
    declared.write_text('example-package==2.0.0\n')
    monkeypatch.setattr(module.importlib.metadata, 'version', lambda name: '1.0.0')
    with pytest.raises(RuntimeError, match='updated lock'):
        module.locked_packages(lock, declared)


def test_locked_installed_versions_are_verified_before_capability_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_verifier()
    lock = tmp_path / 'requirements.lock'
    declared = tmp_path / 'requirements.in'
    lock.write_text('example-package==1.0.0\n')
    declared.write_text('example-package==1.0.0\n')
    monkeypatch.setattr(module.importlib.metadata, 'version', lambda name: '9.0.0')
    with pytest.raises(RuntimeError, match='does not match the lock'):
        module.locked_packages(lock, declared)


def test_distribution_name_case_and_separator_do_not_invalidate_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_verifier()
    lock = tmp_path / 'requirements.lock'
    declared = tmp_path / 'requirements.in'
    lock.write_text('example-package==1.0.0\n')
    declared.write_text('Example_Package==1.0.0\n')
    monkeypatch.setattr(module.importlib.metadata, 'version', lambda name: '1.0.0')
    assert module.locked_packages(lock, declared) == {'example-package': '1.0.0'}
