"""Generate the source-mode proxy from the same privacy rules as the packaged console."""

import argparse
from pathlib import Path


def render(template: str) -> str:
    marker = 'server {'
    if template.count(marker) != 1:
        raise ValueError('Expected a single frontend server')
    connection = "map $http_upgrade $source_connection { default upgrade; '' close; }\n\n"
    result = template.replace(marker, connection + marker, 1)
    proxy = 'proxy_pass http://ui-source:15190;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection $source_connection;\n        proxy_set_header Host $http_host;\n        proxy_set_header Referer $safe_http_referer;\n        proxy_buffering off;\n        proxy_read_timeout 660s;'
    for statement in [
        'try_files /platform.html =404;',
        'try_files $uri /platform.html =404;',
        'try_files $uri $uri/ /platform.html;',
    ]:
        if result.count(statement) != 1:
            raise ValueError('Static routing rule has changed: ' + statement)
        result = result.replace(statement, proxy)
    exact = '    location = /platform.html {'
    if exact not in result:
        raise ValueError('Missing HTML entry location')
    result = result.replace(exact, exact + '\n        ' + proxy, 1)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('template', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.template.read_text()))


if __name__ == '__main__':
    main()
