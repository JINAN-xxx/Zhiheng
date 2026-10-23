from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from zhiheng.api.dependencies import (
    COOKIE_NAME,
    ContainerDependency,
    PrincipalDependency,
    SessionDependency,
)
from zhiheng.shared.principal import Principal

router = APIRouter(prefix='/api/auth', tags=['identity'])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: UUID
    username: str
    is_admin: bool

    @classmethod
    def from_principal(cls, principal: Principal) -> 'UserResponse':
        return cls(id=principal.id, username=principal.username, is_admin=principal.is_admin)


class LoginResponse(BaseModel):
    user: UserResponse


@router.post('/login')
async def login(
    body: LoginRequest, response: Response, container: ContainerDependency, session: SessionDependency
) -> LoginResponse:
    async with session.begin():
        principal, token = await container.identity.login(
            session, username=body.username, password=body.password.get_secret_value()
        )
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=container.settings.cookie_secure,
        samesite='strict',
        max_age=container.settings.session_ttl_seconds,
        path='/api',
    )
    return LoginResponse(user=UserResponse.from_principal(principal))


@router.get('/me')
async def me(principal: PrincipalDependency) -> UserResponse:
    return UserResponse.from_principal(principal)


@router.post('/logout', status_code=204)
async def logout(request: Request, container: ContainerDependency, session: SessionDependency) -> Response:
    async with session.begin():
        await container.identity.logout(session, request.cookies.get(COOKIE_NAME))
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME, path='/api', secure=container.settings.cookie_secure, httponly=True, samesite='strict'
    )
    return response
