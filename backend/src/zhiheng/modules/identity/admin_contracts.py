"""Strict administration request schemas shared by HTTP and identity use cases."""

from typing import ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from zhiheng.modules.identity.admin_permissions import PERMISSION_CODES


class RequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class PatchRequest(RequestModel):
    expected_version: int = Field(ge=1)
    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode='after')
    def reject_null_fields(self) -> 'PatchRequest':
        for field in self.model_fields_set - self.nullable_fields:
            if getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self


class UserCreate(RequestModel):
    username: str = Field(min_length=3, max_length=80, pattern=r'^[a-zA-Z0-9_.@-]+$')
    password: SecretStr = Field(min_length=12, max_length=1024)
    display_name: str = Field(default='', max_length=100)
    email: str = Field(default='', max_length=254)
    phone: str = Field(default='', max_length=40)
    department_id: UUID | None = None
    role_ids: list[UUID] = Field(default_factory=list, max_length=100)
    is_active: bool = True
    is_admin: bool = False
    remark: str = Field(default='', max_length=2000)


class UserPatch(PatchRequest):
    nullable_fields = frozenset({'department_id'})
    display_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    department_id: UUID | None = None
    role_ids: list[UUID] | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    is_admin: bool | None = None
    remark: str | None = Field(default=None, max_length=2000)


class RoleCreate(RequestModel):
    code: str = Field(
        default_factory=lambda: f'role_{uuid4().hex}', min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_.:-]+$'
    )
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=list, max_length=len(PERMISSION_CODES))
    is_active: bool = True
    remark: str = Field(default='', max_length=2000)


class RolePatch(PatchRequest):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    permissions: list[str] | None = Field(default=None, max_length=len(PERMISSION_CODES))
    is_active: bool | None = None
    remark: str | None = Field(default=None, max_length=2000)


class DepartmentCreate(RequestModel):
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(default=0, ge=0, le=1000000)
    leader: str = Field(default='', max_length=100)
    phone: str = Field(default='', max_length=40)
    email: str = Field(default='', max_length=254)
    is_active: bool = True


class DepartmentPatch(PatchRequest):
    nullable_fields = frozenset({'parent_id'})
    parent_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = Field(default=None, ge=0, le=1000000)
    leader: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)
    is_active: bool | None = None


class PasswordReset(RequestModel):
    password: SecretStr = Field(min_length=12, max_length=1024)
    expected_version: int = Field(ge=1)


AdminKind = Literal['users', 'roles', 'departments']
CREATE_SCHEMAS: dict[str, type[BaseModel]] = {
    'users': UserCreate,
    'roles': RoleCreate,
    'departments': DepartmentCreate,
}
PATCH_SCHEMAS: dict[str, type[BaseModel]] = {
    'users': UserPatch,
    'roles': RolePatch,
    'departments': DepartmentPatch,
}
