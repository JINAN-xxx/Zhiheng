import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('entry', ['zhiheng.api.main', 'zhiheng.cli.main'])
def test_import_in_legacy_working_directory_never_loads_dotenv(tmp_path: Path, entry: str) -> None:
    (tmp_path / 'config.ini').write_text('malformed legacy INI sentinel without section headers\n')
    (tmp_path / '.env').write_text('GRAPH_LEGACY_ENV_SENTINEL=must_not_load\nZHIHENG_WORKER_QUEUE=legacy_poison\n')
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '0'}
    env.pop('GRAPH_LEGACY_ENV_SENTINEL', None)
    env.pop('ZHIHENG_WORKER_QUEUE', None)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    script = r"""
import importlib,os
importlib.import_module(ENTRY)
assert os.environ.get('GRAPH_LEGACY_ENV_SENTINEL') is None
assert os.environ.get('ZHIHENG_WORKER_QUEUE') is None
assert os.environ['PYTHON_DOTENV_DISABLED']=='1'
from zhiheng.modules.knowledge.adapters.lightrag.registry import register
from zhiheng.modules.knowledge.adapters.lightrag import policy
import configparser,threading
from pathlib import Path
from importlib.metadata import distribution
register()
assert policy.install() is False
importlib.reload(policy)
assert policy.install() is False
assert 'malformed legacy' in Path('config.ini').read_text()
Path('config.ini').write_text('[ordinary]\nvalue=allowed\n')
ordinary=configparser.ConfigParser()
assert ordinary.read('config.ini')==['config.ini']
assert ordinary['ordinary']['value']=='allowed'
Path('neighbor.ini').write_text('adjacent allowed')
filename=str(Path(distribution('lightrag-hku').locate_file('lightrag/kg/milvus_impl.py')).resolve())
frame={'__name__':'lightrag.kg.milvus_impl'}
for path in (str(Path('config.ini').resolve()),'neighbor.ini'):
    exec(compile('result=open(path).read()',filename,'exec'),{**frame,'path':path})
try:
    exec(compile("open('config.ini').read()",filename,'exec'),frame)
except FileNotFoundError:
    pass
else:
    raise AssertionError('SDK relative INI read was permitted')
try:Path('missing-neighbor.ini').read_text()
except FileNotFoundError as error:assert error.filename=='missing-neighbor.ini'
else:raise AssertionError('Neighbor error was swallowed')
errors=[]
def reload_sdk():
    try:importlib.reload(importlib.import_module('lightrag.kg.milvus_impl'))
    except BaseException as error:errors.append(type(error).__name__)
thread=threading.Thread(target=reload_sdk);thread.start();thread.join(20)
assert not thread.is_alive() and not errors
assert importlib.import_module('lightrag.kg.milvus_impl').config.sections()==[]
register()
""".replace('ENTRY', repr(entry))
    result = subprocess.run(
        [sys.executable, '-B', '-c', script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
