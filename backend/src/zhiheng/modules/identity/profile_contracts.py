"""Public values for the authenticated user's own profile and credentials."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ProfileInfo(BaseModel):
    id: UUID
    username: str
    display_name: str
    email: str
    phone: str
    is_admin: bool
    department_name: str | None
    roles: list[str]
    posts: list[str]
    created_at: datetime | None
    version: int


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    display_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    expected_version: int = Field(ge=1, strict=True)

    @model_validator(mode='after')
    def reject_null_fields(self) -> 'ProfilePatch':
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')

    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)
    expected_version: int = Field(ge=1, strict=True)
