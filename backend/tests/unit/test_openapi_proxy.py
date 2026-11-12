"""Both proxy templates route grouped schemas as API traffic, including source mode."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_nginx_and_rendered_source_proxy_route_grouped_schema_namespace() -> None:
    spec = importlib.util.spec_from_file_location('source_proxy', ROOT / 'tools/source_proxy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = (ROOT / 'frontend/nginx.core.conf').read_text()
    pattern = next(
        line.split(' ~ ', 1)[1].rsplit(' {', 1)[0]
        for line in config.splitlines()
        if 'location ~ ' in line and '(api|' in line
    )
    for path in ('/openapi.json', '/openapi/resources-v1.json', '/openapi/inference-v1.json', '/docs/resources'):
        assert re.search(pattern, path), path
    assert not re.search(pattern, '/developer')
    rendered = module.render(config)
    assert pattern in rendered and 'proxy_pass http://ui-source:15190' in rendered
    assert 'proxy_set_header Referer $safe_http_referer' in rendered
