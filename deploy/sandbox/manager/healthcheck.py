import os
from http import HTTPStatus
from urllib.request import urlopen

import docker

client = docker.from_env()
try:
    gateway = client.networks.get('bridge').attrs['IPAM']['Config'][0]['Gateway']
finally:
    client.close()
port = int(os.environ.get('ZHIHENG_SANDBOX_MANAGER_PORT', '19192'))
with urlopen(f'http://{gateway}:{port}/health', timeout=2) as response:
    if response.status != HTTPStatus.OK:
        raise SystemExit(1)
