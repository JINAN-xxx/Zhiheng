"""Optional exact test-resource journal; never records connection secrets."""

import json
import os
from pathlib import Path


def resource(action: str, kind: str, name: str, **identity: object) -> None:
    path = os.getenv('ZHIHENG_TEST_RESOURCE_LOG')
    if path:
        with Path(path).open('a') as stream:
            stream.write(
                json.dumps({'action': action, 'kind': kind, 'name': name, 'process_id': os.getpid(), **identity}) + '\n'
            )
