# ruff: noqa: E402 -- Official application construction follows configuration and policy installation.
"""Launch the official server with platform-owned bounded scratch policy.

OpenSandbox 1.2.1 Docker storage-opt quotas require XFS pquota. The narrow host-config
hook below uses kernel-enforced tmpfs mounts on ext4 too; model processes use the
official strict isolated-session API. Lifecycle, authentication, TTL and egress remain
implemented by the pinned upstream server.
"""

import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import docker
from docker.errors import NotFound
from fastapi import HTTPException
from opensandbox_server.services.constants import SANDBOX_MANAGED_VOLUMES_LABEL
from opensandbox_server.services.docker.container_ops import DockerContainerOpsMixin

original_host_config = DockerContainerOpsMixin._base_host_config_kwargs
original_docker_host_config = docker.APIClient.create_host_config
dns_servers = [
    str(ipaddress.ip_address(value.strip()))
    for value in os.environ.get('ZHIHENG_SANDBOX_DNS_SERVERS', '223.5.5.5,1.1.1.1').split(',')
    if value.strip()
]
workspace_mb = int(os.environ.get('ZHIHENG_SANDBOX_WORKSPACE_MB', '1024'))
MIN_WORKSPACE_MB = 64
MAX_WORKSPACE_MB = 4096
if not MIN_WORKSPACE_MB <= workspace_mb <= MAX_WORKSPACE_MB:
    raise ValueError('Sandbox workspace size must be between 64 and 4096 MiB')


def bounded_host_config(
    self: Any,
    mem_limit: int | None,
    nano_cpus: int | None,
    network_mode: str,
    gpu_count: int | None = None,
    disk_quota: int | None = None,
) -> dict[str, Any]:
    if disk_quota is not None:
        raise ValueError('Use platform bounded tmpfs scratch instead of unsupported Docker writable-layer quotas')
    result = original_host_config(self, mem_limit, nano_cpus, network_mode, gpu_count, None)
    result['tmpfs'] = {
        # Docker tmpfs defaults to noexec; native wheel extensions need executable mappings.
        '/workspace': f'rw,exec,nosuid,nodev,size={workspace_mb}m,uid=0,gid=0,mode=0755',
        '/tmp': 'rw,nosuid,nodev,size=128m,mode=1777',
        '/run/zhiheng': 'rw,nosuid,nodev,size=128m,uid=0,gid=0,mode=0700',
    }
    return result


DockerContainerOpsMixin._base_host_config_kwargs = bounded_host_config


def reachable_sidecar_dns(self: Any, *args: Any, **kwargs: Any) -> Any:
    # WSL's DNS-tunnel address is host-only; a bridge container needs reachable
    # resolvers. Egress sidecars include precisely these DNS IPs in their policy.
    if kwargs.get('network_mode') == 'bridge' and kwargs.get('cap_add') == ['NET_ADMIN'] and dns_servers:
        kwargs['dns'] = dns_servers
    return original_docker_host_config(self, *args, **kwargs)


docker.APIClient.create_host_config = reachable_sidecar_dns
if not os.environ.get('OPENSANDBOX_SERVER_API_KEY'):
    raise ValueError('Set an independent OpenSandbox manager API key')
client = docker.from_env()
gateway = client.networks.get('bridge').attrs['IPAM']['Config'][0]['Gateway']
client.close()
port = int(os.environ.get('ZHIHENG_SANDBOX_MANAGER_PORT', '19192'))
execd = os.environ.get('ZHIHENG_SANDBOX_EXECD_IMAGE', 'opensandbox/execd:release-1.2.1')
egress = os.environ.get('ZHIHENG_SANDBOX_EGRESS_IMAGE', 'opensandbox/egress:release-1.2.1')
config = Path('/tmp/zhiheng-opensandbox.toml')
config.write_text(f"""
[server]
host = {json.dumps(gateway)}
port = {port}
max_sandbox_timeout_seconds = 3600
[proxy]
resolve_internal = true
[runtime]
type = "docker"
execd_image = {json.dumps(execd)}
[docker]
network_mode = "bridge"
host_ip = {json.dumps(gateway)}
publish_host = {json.dumps(gateway)}
drop_capabilities = ["AUDIT_WRITE", "MKNOD", "NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_MODULE", "SYS_PTRACE", "SYS_TIME", "SYS_TTY_CONFIG"]
no_new_privileges = true
pids_limit = 128
port_range_min = 41000
port_range_max = 42000
[storage]
allowed_host_paths = []
[egress]
image = {json.dumps(egress)}
mode = "dns+nft"
[log]
level = "WARNING"
""")
config.chmod(0o600)
os.environ['SANDBOX_CONFIG_PATH'] = str(config)

from opensandbox_server.main import (
    app,
)


@app.get('/platform/profile')
def platform_profile() -> dict[str, object]:
    return {
        'policy_version': 1,
        'manager': 'opensandbox-1.2.1',
        'workspace_bytes': workspace_mb * 1024 * 1024,
        'isolated_userns_required': True,
        'model_network': 'private',
        'task_host_binds': False,
    }


@app.delete('/platform/sandboxes/{sandbox_id}/residue')
def remove_owned_residue(sandbox_id: str) -> dict[str, bool]:
    try:
        UUID(sandbox_id)
    except ValueError as error:
        raise HTTPException(422, 'Invalid sandbox identity') from error
    client = docker.from_env()
    try:
        sidecars = client.containers.list(
            all=True, filters={'label': f'opensandbox.io/egress-sidecar-for={sandbox_id}'}
        )
        for sidecar in sidecars:
            try:
                sidecar.remove(force=True)
            except NotFound:  # noqa: PERF203 - cleanup remains idempotent across concurrent TTL collection
                pass
        try:
            volume = client.volumes.get('opensandbox-runtime-' + sandbox_id)
        except NotFound:
            volume = None
        if volume is not None:
            if (volume.attrs.get('Labels') or {}).get(SANDBOX_MANAGED_VOLUMES_LABEL) != 'server':
                raise HTTPException(503, 'Runtime volume ownership is unconfirmed')
            try:
                volume.remove()
            except NotFound:
                pass
        remaining = client.containers.list(
            all=True, filters={'label': f'opensandbox.io/egress-sidecar-for={sandbox_id}'}
        )
        if remaining:
            raise HTTPException(503, 'Sandbox cleanup is not confirmed')
        return {'clean': True}
    finally:
        client.close()


sys.argv = ['opensandbox-server', '--config', str(config)]
from opensandbox_server.cli import main

main()
