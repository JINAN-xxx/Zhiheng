import importlib
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_release_infrastructure_uses_installation_scoped_configuration() -> None:
    services = yaml.safe_load((ROOT / 'deploy/compose.release.yaml').read_text())['services']
    for name in ('litellm-proxy', 'milvus'):
        configuration_mounts = [value for value in services[name]['volumes'] if '.yaml:' in value]
        assert configuration_mounts
        assert all(value.startswith('${RELEASE_CONFIG_DIR:') for value in configuration_mounts)


def test_runtime_configuration_is_stable_and_tampering_is_rejected(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / 'deploy'))
    manager_module = importlib.import_module('release.manager')
    manager = manager_module.ReleaseManager(tmp_path / 'site')
    first = tmp_path / 'v1'
    second = tmp_path / 'v2'
    for bundle in (first, second):
        path = bundle / 'deploy/litellm/config.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('model_list: []\n')
    manager.prepare_configuration(first)
    manager.prepare_configuration(second)
    stable = tmp_path / 'site/runtime/litellm/config.yaml'
    assert stable.read_text() == 'model_list: []\n'
    stable.write_text('modified')
    with pytest.raises(manager_module.ReleaseError, match='配置'):
        manager.prepare_configuration(second)
    assert stable.read_text() == 'modified'
