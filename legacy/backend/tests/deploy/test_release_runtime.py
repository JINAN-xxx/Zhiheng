import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def runtime_module() -> ModuleType:
    sys.path.insert(0, str(ROOT / 'deploy'))
    return importlib.import_module('release.runtime')


def test_loading_cannot_replace_an_existing_different_image_tag(tmp_path: Path) -> None:
    module = runtime_module()
    runtime = module.DockerRuntime(tmp_path)
    calls = []

    def run(command: list[str], **kwargs) -> str:
        calls.append(command)
        if command[1:3] == ['image', 'ls']:
            return 'sha256:' + '2' * 64
        return json.dumps({'Id': 'sha256:' + '1' * 64, 'Os': 'linux', 'Architecture': 'amd64'})

    runtime.run = run
    manifest = {'platform': 'linux/amd64', 'images': {'backend': {'tag': 'fixed:v1', 'id': 'sha256:' + '1' * 64}}}
    with pytest.raises(module.ReleaseError, match='已有镜像'):
        runtime.load_images(tmp_path, manifest)
    assert not any(command[1] == 'load' for command in calls)


def test_recovery_restores_then_clears_only_the_target_ready_marker(tmp_path: Path) -> None:
    module = runtime_module()
    runtime = module.DockerRuntime(tmp_path)
    runtime.compose = ['docker', 'compose']
    calls = []
    runtime.run = lambda command, **kwargs: calls.append(command) or ''
    runtime.start_applications()
    assert calls[0][-1] == 'clear-marker'
    assert '--no-deps' in calls[0]
    assert [command[-1] for command in calls[1:]] == ['zhiheng-backend-pg', 'zhiheng-frontend']
    assert all('--no-deps' in command and '--wait' in command for command in calls[1:])


def test_proxy_gate_rejects_html_with_status_200(tmp_path: Path) -> None:
    module = runtime_module()
    runtime = module.DockerRuntime(tmp_path)
    runtime.compose = ['docker', 'compose']
    runtime.manifest = {'version': 'v1', 'images': {'backend': {'id': 'backend'}, 'frontend': {'id': 'frontend'}}}
    runtime._probe = lambda *args, **kwargs: {'plugins': [], 'migrations': []}
    runtime.run = lambda *args, **kwargs: '<html>wrong upstream</html>'
    runtime.containers = lambda: [
        {
            'Config': {
                'Labels': {'com.docker.compose.service': 'zhiheng-' + role + ('-pg' if role == 'backend' else '')}
            },
            'State': {'Status': 'running'},
            'Image': role,
        }
        for role in ('backend', 'frontend')
    ]
    with pytest.raises(module.ReleaseError, match='反代'):
        runtime.verify()
