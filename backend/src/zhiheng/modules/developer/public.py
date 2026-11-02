from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.developer.api_requests import APIOrigin, APIRequestInfo, APIRequestService
from zhiheng.modules.developer.applications import ApplicationService
from zhiheng.modules.developer.contracts import (
    ApplicationCreate,
    ApplicationInfo,
    ApplicationPatch,
    IssuedKey,
    KeyCreate,
    KeyInfo,
    KeyRotate,
    LegacyReservation,
    ResourceGrantRemove,
    ResourceGrantRequest,
    ResourceQuotaRequest,
    UsageOutcome,
    UsageSummary,
)
from zhiheng.modules.developer.keys import DeveloperIdentity, KeyService
from zhiheng.modules.developer.orm import (
    APIRequest,
    DeveloperApplication,
    DeveloperKey,
    Invocation,
    KeyMinuteBucket,
    ResourceGrant,
    ResourceMinuteBucket,
    SourceEnrollment,
    UsageAccount,
)
from zhiheng.modules.developer.resource_grants import (
    ResourceGrantInfo,
    ResourceGrantService,
    ResourceOwnership,
    SourceFileFact,
)
from zhiheng.modules.developer.resource_limits import ResourceLimitService
from zhiheng.modules.developer.scopes import SCOPES, ResourceAction, ResourceKind, Scope, resource_scope, scope_catalog
from zhiheng.modules.developer.service import UsageService

__all__ = [
    'SCOPES',
    'APIOrigin',
    'APIRequestInfo',
    'APIRequestService',
    'ApplicationCreate',
    'ApplicationInfo',
    'ApplicationPatch',
    'ApplicationService',
    'DeveloperIdentity',
    'IssuedKey',
    'KeyCreate',
    'KeyInfo',
    'KeyRotate',
    'KeyService',
    'LegacyReservation',
    'ResourceAction',
    'ResourceGrantInfo',
    'ResourceGrantRemove',
    'ResourceGrantRequest',
    'ResourceGrantService',
    'ResourceKind',
    'ResourceLimitService',
    'ResourceOwnership',
    'ResourceQuotaRequest',
    'Scope',
    'SourceFileFact',
    'UsageOutcome',
    'UsageService',
    'UsageSummary',
    'resource_scope',
    'schema_tables',
    'scope_catalog',
]


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (
        Base.metadata.tables[UsageAccount.__tablename__],
        Base.metadata.tables[Invocation.__tablename__],
        Base.metadata.tables[DeveloperApplication.__tablename__],
        Base.metadata.tables[DeveloperKey.__tablename__],
        Base.metadata.tables[KeyMinuteBucket.__tablename__],
        *(
            Base.metadata.tables[row.__tablename__]
            for row in (APIRequest, ResourceGrant, ResourceMinuteBucket, SourceEnrollment)
        ),
    )
