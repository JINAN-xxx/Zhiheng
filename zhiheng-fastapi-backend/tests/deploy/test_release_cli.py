import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_release_help_is_available_from_existing_entrypoint() -> None:
    result = subprocess.run(
        ['sh', str(ROOT / 'deploy.sh'), 'release', '--help'], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    for action in ('build', 'verify', 'deploy', 'rollback', 'status'):
        assert action in result.stdout


def test_status_needs_no_docker_or_environment_generation(tmp_path: Path) -> None:
    result = subprocess.run(
        ['sh', str(ROOT / 'deploy.sh'), 'release', 'status', '--site', str(tmp_path / 'site')],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '未部署' in result.stdout
    assert not (tmp_path / 'site').exists()
