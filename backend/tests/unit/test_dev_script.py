"""Verify source instance selection without contacting a Docker daemon."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def source_instances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    roots = [tmp_path / name for name in ('first checkout', 'second checkout')]
    paths = ('dev.sh', 'tools/source_proxy.py', 'frontend/nginx.core.conf')
    for root in roots:
        for relative in paths:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        environment = root / 'deploy/core/.env.core'
        environment.parent.mkdir(parents=True)
        environment.touch()

    commands = tmp_path / 'commands.jsonl'
    executables = tmp_path / 'bin'
    executables.mkdir()
    docker = executables / 'docker'
    docker.write_text(
        f'#!{sys.executable}\n'
        """
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ['TEST_DOCKER_CALLS'], 'a') as commands:
    commands.write(json.dumps(args) + '\\n')
match args:
    case ['ps', *_]:
        assert 'label=com.docker.compose.oneoff=False' in args
        print(os.environ.get('TEST_DOCKER_API', 'api-id'))
    case ['inspect', '--format', template, 'api-id']:
        if '/app/src' in template:
            print(os.environ['TEST_DOCKER_SOURCE'] + '/backend/src')
        elif '/app/migrations' in template:
            print(os.environ['TEST_DOCKER_SOURCE'] + '/backend/migrations')
        elif 'project.environment_file' in template:
            print(os.environ['TEST_DOCKER_ENV'])
        else:
            sys.exit(2)
    case ['compose', *_]:
        pass
    case _:
        sys.exit(2)
"""
    )
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(executables) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('TEST_DOCKER_CALLS', str(commands))
    monkeypatch.setenv('TEST_DOCKER_SOURCE', str(roots[0]))
    monkeypatch.setenv('TEST_DOCKER_ENV', str(roots[0] / 'deploy/core/.env.core'))
    return roots[0], roots[1], commands


def run_source(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['bash', 'dev.sh', *args], cwd=root, text=True, capture_output=True, check=False)


def test_implicit_source_projects_are_isolated(source_instances: tuple[Path, Path, Path]) -> None:
    first, second, commands = source_instances
    first_name = run_source(first, 'project-name')
    second_name = run_source(second, 'project-name')
    extra_env = first / 'other.env'
    extra_env.touch()
    third_name = run_source(first, '--env-file', str(extra_env), 'project-name')
    assert all(item.returncode == 0 for item in (first_name, second_name, third_name))
    assert len({first_name.stdout, second_name.stdout, third_name.stdout}) == 3
    assert all(item.stdout.startswith('zhiheng-dev-') for item in (first_name, second_name, third_name))
    assert not commands.exists()

    assert run_source(second, 'ps').returncode == 0
    compose_args = json.loads(commands.read_text().strip())
    assert compose_args[0] == 'compose'
    assert compose_args[compose_args.index('--project-name') + 1] == second_name.stdout.strip()
    assert compose_args[compose_args.index('--env-file') + 1] == str(second / 'deploy/core/.env.core')


def test_existing_project_requires_matching_checkout_and_environment(source_instances: tuple[Path, Path, Path]) -> None:
    first, second, commands = source_instances
    wrong_source = run_source(second, '--attach-existing', 'zhiheng-core', 'migrate')
    assert wrong_source.returncode == 2
    assert 'not running source mode from this checkout' in wrong_source.stderr
    wrong_env = run_source(
        first, '--env-file', str(second / 'deploy/core/.env.core'), '--attach-existing', 'zhiheng-core', 'up'
    )
    assert wrong_env.returncode == 2
    assert 'environment file differs' in wrong_env.stderr
    assert all(json.loads(line)[0] != 'compose' for line in commands.read_text().splitlines())

    same_source = run_source(first, '--attach-existing', 'zhiheng-core', 'restart-worker')
    assert same_source.returncode == 0, same_source.stderr
    compose_args = json.loads(commands.read_text().splitlines()[-1])
    assert compose_args[-4:] == ['--project-name', 'zhiheng-core', 'restart', 'worker']


def test_attach_fails_closed_without_one_api(
    source_instances: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _, commands = source_instances
    monkeypatch.setenv('TEST_DOCKER_API', 'first-id\nsecond-id')
    result = run_source(first, '--attach-existing', 'zhiheng-core', 'up')
    assert result.returncode == 2
    assert 'Expected one existing API container' in result.stderr
    assert [json.loads(line)[0] for line in commands.read_text().splitlines()] == ['ps']
