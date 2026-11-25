"""Provider-independent parsing settings, with encrypted credentials and version checks."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.files.parsing_contracts import ParsingSettingsInfo, ParsingSettingsPatch
from zhiheng.modules.files.parsing_orm import ParsingConfiguration
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

DEFAULT_PARSING: dict[str, object] = {
    'local_url': None,
    'local_key': None,
    'cloud_enabled': False,
    'cloud_url': None,
    'cloud_key': None,
    'tier': 'standard',
    'timeout_seconds': 900,
    'queue_timeout_seconds': 120,
}


class ParsingSettingsService:
    def __init__(self, cipher: CredentialCipher) -> None:
        self.cipher = cipher

    async def snapshot(self, session: AsyncSession) -> tuple[dict[str, object], int]:
        row = await session.get(ParsingConfiguration, 1)
        return (
            {**DEFAULT_PARSING, **(row.config if row else {}), 'policy_revision': row.version if row else 1},
            row.version if row else 1,
        )

    def public(self, config: dict[str, object], version: int) -> ParsingSettingsInfo:
        return ParsingSettingsInfo.model_validate(
            {
                **{key: value for key, value in config.items() if key not in {'local_key', 'cloud_key'}},
                'has_local_key': bool(config.get('local_key')),
                'has_cloud_key': bool(config.get('cloud_key')),
                'version': version,
            }
        )

    async def get(self, session: AsyncSession, principal: Principal) -> ParsingSettingsInfo:
        principal.require_permission('platform:knowledge_settings:read')
        config, version = await self.snapshot(session)
        return self.public(config, version)

    async def update(
        self, session: AsyncSession, principal: Principal, body: ParsingSettingsPatch
    ) -> ParsingSettingsInfo:
        principal.require_permission('platform:knowledge_settings:update')
        await session.execute(
            insert(ParsingConfiguration).values(id=1, version=1, config=DEFAULT_PARSING).on_conflict_do_nothing()
        )
        row = await session.get(ParsingConfiguration, 1, with_for_update=True, populate_existing=True)
        assert row is not None
        if row.version != body.expected_version:
            raise AppError('parsing_settings_conflict', '解析配置已变化，请刷新后重试', 409)
        values = body.model_dump(
            exclude_unset=True,
            exclude={'expected_version', 'local_api_key', 'cloud_api_key', 'clear_local_key', 'clear_cloud_key'},
        )
        config = {**DEFAULT_PARSING, **row.config, **values}
        for prefix in ('local', 'cloud'):
            if row.config.get(prefix + '_url') != config.get(prefix + '_url'):
                config[prefix + '_key'] = None
            key = body.local_api_key if prefix == 'local' else body.cloud_api_key
            clear = body.clear_local_key if prefix == 'local' else body.clear_cloud_key
            if clear:
                config[prefix + '_key'] = None
            elif key is not None and key.get_secret_value():
                config[prefix + '_key'] = self.cipher.encrypt(key.get_secret_value())
        if config['cloud_enabled'] and not config['cloud_url']:
            raise AppError('invalid_parsing_settings', '启用云端后备前请填写云端服务地址', 422)
        row.config, row.version = config, row.version + 1
        return self.public(config, row.version)

    def key(self, config: dict[str, object], engine: str) -> str:
        value = config.get(engine + '_key')
        return self.cipher.decrypt(value) if isinstance(value, str) and value else ''
