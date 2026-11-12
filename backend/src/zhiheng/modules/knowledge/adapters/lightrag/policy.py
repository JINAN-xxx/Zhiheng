"""Process policy for the locked SDK's implicit working-directory configuration.

Python audit hooks cannot be removed. This hook intentionally lasts for the
process, including imports on other threads. Spawned interpreters install it
again through the adapter entry point; normal imports/reloads install it once.
It affects only reads of the exact relative name config.ini originating in the
installed SDK's two known configuration-reading source files. Explicit absolute
paths, adjacent files and callers outside those SDK frames remain untouched.
The official SDK bytes and Python's config parser are never modified.
"""

import os
import sys
from importlib.metadata import distribution
from pathlib import Path

if '_STATE' not in globals():
    _STATE: list[bool] = [False]
_MIN_OPEN_ARGUMENTS = 2


def install() -> bool:
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    if _STATE[0]:
        return False
    package = distribution('lightrag-hku')
    sources = {
        name: str(Path(str(package.locate_file(name.replace('.', '/') + '.py'))).resolve())
        for name in ('lightrag.kg.milvus_impl', 'lightrag.kg.postgres_impl')
    }

    def block_sdk_ini(event: str, arguments: tuple[object, ...]) -> None:
        if event != 'open' or len(arguments) < _MIN_OPEN_ARGUMENTS or arguments[0] != 'config.ini':
            return
        mode = arguments[1]
        if not isinstance(mode, str) or not mode.startswith('r'):
            return
        frame = sys._getframe(1)
        while frame is not None:
            module = frame.f_globals.get('__name__')
            if isinstance(module, str) and sources.get(module) == frame.f_code.co_filename:
                raise FileNotFoundError('Implicit SDK config.ini discovery is disabled')
            parent = frame.f_back
            if parent is None:
                break
            frame = parent

    sys.addaudithook(block_sdk_ini)
    _STATE[0] = True
    return True
