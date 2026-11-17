"""Compose the shared execution adapter without enabling a host execution fallback."""

from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.sandbox.adapters.opensandbox import OpenSandboxFactory
from zhiheng.infrastructure.sandbox.contracts import SandboxFactory


def create_sandbox_factory(settings: Settings) -> SandboxFactory:
    return OpenSandboxFactory(
        settings.sandbox_url,
        settings.sandbox_api_key.get_secret_value(),
        settings.sandbox_image,
        cpu=settings.sandbox_cpu,
        memory_mb=settings.sandbox_memory_mb,
        lifetime_seconds=settings.sandbox_run_seconds,
        ready_seconds=settings.sandbox_ready_seconds,
        pip_index_url=settings.sandbox_pip_index_url,
    )
