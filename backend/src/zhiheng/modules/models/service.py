"""Explicit composition of model configuration, catalog and execution owners."""

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.models.bindings import ModelBindings
from zhiheng.modules.models.catalog import ModelCatalog
from zhiheng.modules.models.configuration import ModelConfigurationCommands
from zhiheng.modules.models.connections import ModelConnections
from zhiheng.modules.models.contracts import ProviderFactory
from zhiheng.modules.models.rates import ModelRates


class ModelService:
    def __init__(self, cipher: CredentialCipher, timeout_seconds: float, *, provider_factory: ProviderFactory) -> None:
        self.connections = ModelConnections(cipher)
        self.catalog = ModelCatalog()
        self.bindings = ModelBindings(cipher, timeout_seconds, provider_factory=provider_factory)
        self.configuration = ModelConfigurationCommands(self.catalog, self.bindings)
        self.rates = ModelRates()
