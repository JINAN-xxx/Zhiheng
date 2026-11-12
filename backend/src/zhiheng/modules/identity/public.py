from zhiheng.modules.identity.admin_contracts import AdminKind, PasswordReset
from zhiheng.modules.identity.admin_permissions import permission_catalog
from zhiheng.modules.identity.admin_service import IdentityAdminService
from zhiheng.modules.identity.contracts import IdentityDirectoryEntry, IdentityDirectoryPage, IdentityInfo
from zhiheng.modules.identity.profile_contracts import PasswordChange, ProfileInfo, ProfilePatch
from zhiheng.modules.identity.service import IdentityService

"""Identity use cases; callers receive principals, never identity ORM objects."""


__all__ = [
    'AdminKind',
    'IdentityAdminService',
    'IdentityDirectoryEntry',
    'IdentityDirectoryPage',
    'IdentityInfo',
    'IdentityService',
    'PasswordChange',
    'PasswordReset',
    'ProfileInfo',
    'ProfilePatch',
    'permission_catalog',
]
