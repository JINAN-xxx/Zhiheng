"""Explicit canonical role grants for mocked browser identities and role catalogs."""
from pathlib import Path
import runpy

_POLICY = runpy.run_path(str(Path(__file__).resolve().parents[3] / 'backend/src/zhiheng/shared/permission_policy.py'))
PERMISSION_CATALOG = _POLICY['catalog']()
_CANONICAL_CODES = {item['code'] for item in PERMISSION_CATALOG}


def granted_permissions(*codes):
    result = set()
    for code in codes:
        if code not in _CANONICAL_CODES:
            raise ValueError('Test grants require a canonical permission: ' + code)
        module, action = code.rsplit(':', 1)
        result.add(module + ':enabled')
        if action != 'enabled':
            result.add(module + ':read')
        result.add(code)
    return sorted(result)
