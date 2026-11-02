from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from zhiheng.api.common.dependencies import (
    COOKIE_NAME,
    ContainerDependency,
    PrincipalDependency,
    SessionDependency,
)
from zhiheng.modules.identity.public import PasswordChange, ProfileInfo, ProfilePatch
from zhiheng.shared.errors import AppError
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
    permissions: list[str] = Field(default_factory=list)

    @classmethod
    def from_principal(cls, principal: Principal) -> 'UserResponse':
        return cls(
            id=principal.id,
            username=principal.username,
            is_admin=principal.is_admin,
            permissions=sorted(principal.permissions),
        )


class LoginResponse(BaseModel):
    user: UserResponse


@router.post('/login')
async def login(
    body: LoginRequest, response: Response, container: ContainerDependency, session: SessionDependency
) -> LoginResponse:
    try:
        async with session.begin():
            principal, token = await container.identity.login(
                session, username=body.username, password=body.password.get_secret_value()
            )
            await container.operations.record(
                session, principal=principal, action='login', kind='login', message='登录成功'
            )
    except AppError:
        async with session.begin():
            await container.operations.record(
                session,
                principal=None,
                username=body.username,
                action='login',
                kind='login',
                outcome='failed',
                message='登录验证失败',
            )
        raise
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


@router.get('/profile')
async def profile(
    principal: PrincipalDependency, container: ContainerDependency, session: SessionDependency
) -> ProfileInfo:
    async with session.begin():
        return await container.identity.profile(session, user_id=principal.id)


@router.patch('/profile')
async def update_profile(
    body: ProfilePatch,
    principal: PrincipalDependency,
    container: ContainerDependency,
    session: SessionDependency,
) -> ProfileInfo:
    async with session.begin():
        result = await container.identity.update_profile(session, user_id=principal.id, body=body)
        await container.operations.record(
            session,
            principal=principal,
            action='profile.update',
            resource_type='user',
            resource_id=str(principal.id),
            message='已更新本人资料',
        )
    return result


@router.post('/password', status_code=204)
async def change_password(
    body: PasswordChange,
    principal: PrincipalDependency,
    container: ContainerDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.identity.change_password(session, user_id=principal.id, body=body)
        await container.operations.record(
            session,
            principal=principal,
            action='profile.change_password',
            resource_type='user',
            resource_id=str(principal.id),
            message='已更新本人登录凭据并撤销会话',
        )
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME, path='/api', secure=container.settings.cookie_secure, httponly=True, samesite='strict'
    )
    return response


@router.post('/logout', status_code=204)
async def logout(request: Request, container: ContainerDependency, session: SessionDependency) -> Response:
    async with session.begin():
        await container.identity.logout(session, request.cookies.get(COOKIE_NAME))
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME, path='/api', secure=container.settings.cookie_secure, httponly=True, samesite='strict'
    )
    return response
