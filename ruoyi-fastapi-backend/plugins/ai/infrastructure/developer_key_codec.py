import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import NoReturn

from pydantic import SecretStr

from plugins.ai.domain.resource_id import validate_resource_id

TOKEN_BYTES = 32
DIGEST_HEX_LENGTH = 64
MINIMUM_PEPPER_LENGTH = 32
FINGERPRINT_LENGTH = 4
KEY_PATTERN = re.compile(r'^sk-zh_(key_[0-9a-f]{32})_([A-Za-z0-9_-]{43})$')
DIGEST_PATTERN = re.compile(r'^[0-9a-f]{64}$')
PREFIX_PATTERN = re.compile(r'^sk-zh_key_[0-9a-f]{32}$')
FINGERPRINT_PATTERN = re.compile(r'^[A-Za-z0-9_-]{4}$')


class DeveloperKeyCodecError(RuntimeError):
    """不携带密钥材料的固定Codec错误。"""

    __slots__ = ()

    def __init__(self) -> None:
        Exception.__init__(self)

    def __str__(self) -> str:
        return 'Developer API Key 编码失败'


@dataclass(frozen=True, slots=True)
class IssuedDeveloperKey:
    key_id: str
    prefix: str
    fingerprint: str
    digest: str = field(repr=False)
    digest_version: str
    complete_key: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _IssuedMaterial:
    secret_token: str = field(repr=False)
    complete_key: str = field(repr=False)
    digest: str = field(repr=False)


def _issue_material(
    *,
    key_id: str,
    pepper: SecretStr,
    token_bytes: Callable[[int], bytes],
) -> tuple[bool, _IssuedMaterial | None]:
    try:
        random_bytes = token_bytes(TOKEN_BYTES)
        if not isinstance(random_bytes, bytes) or len(random_bytes) != TOKEN_BYTES:
            return True, None
        secret_token = base64.urlsafe_b64encode(random_bytes).rstrip(b'=').decode('ascii')
        complete_key = f'sk-zh_{key_id}_{secret_token}'
        digest = hmac.new(
            pepper.get_secret_value().encode('utf-8'),
            complete_key.encode('ascii'),
            hashlib.sha256,
        ).hexdigest()
        return False, _IssuedMaterial(
            secret_token=secret_token,
            complete_key=complete_key,
            digest=digest,
        )
    except Exception:
        return True, None


def _raise_codec_error() -> NoReturn:
    raise DeveloperKeyCodecError from None


class DeveloperKeyCodec:
    """生成和验证Platform托管的不透明Developer API Key。"""

    __slots__ = ('_current_version', '_peppers', '_token_bytes')

    def __init__(
        self,
        *,
        current_version: str,
        peppers: Mapping[str, SecretStr],
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._current_version = current_version
        self._peppers = dict(peppers)
        self._token_bytes = token_bytes

    def issue(self, key_id: str) -> IssuedDeveloperKey:
        validate_resource_id(key_id, 'key')
        pepper = self._valid_pepper(self._current_version)
        if pepper is None:
            _raise_codec_error()
        failed, material = _issue_material(
            key_id=key_id,
            pepper=pepper,
            token_bytes=self._token_bytes,
        )
        if failed or material is None:
            _raise_codec_error()
        return IssuedDeveloperKey(
            key_id=key_id,
            prefix=f'sk-zh_{key_id}',
            fingerprint=material.secret_token[-FINGERPRINT_LENGTH:],
            digest=material.digest,
            digest_version=self._current_version,
            complete_key=material.complete_key,
        )

    @staticmethod
    def identify(complete_key: object) -> tuple[str, str] | None:
        if not isinstance(complete_key, str):
            return None
        matched = KEY_PATTERN.fullmatch(complete_key)
        if matched is None:
            return None
        key_id = matched.group(1)
        return key_id, f'sk-zh_{key_id}'

    def verify(self, complete_key: str, *, version: str, expected_digest: str) -> bool:
        if not isinstance(complete_key, str) or KEY_PATTERN.fullmatch(complete_key) is None:
            return False
        if not isinstance(expected_digest, str) or DIGEST_PATTERN.fullmatch(expected_digest) is None:
            return False
        pepper = self._valid_pepper(version)
        if pepper is None:
            return False
        try:
            actual_digest = hmac.new(
                pepper.get_secret_value().encode('utf-8'),
                complete_key.encode('ascii'),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(actual_digest, expected_digest)
        except Exception:
            return False

    def mask(self, prefix: str, fingerprint: str) -> str:
        if not isinstance(prefix, str) or PREFIX_PATTERN.fullmatch(prefix) is None:
            raise ValueError('API Key前缀格式无效')
        if not isinstance(fingerprint, str) or FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise ValueError('API Key指纹格式无效')
        return f'{prefix[:14]}...{fingerprint}'

    def _valid_pepper(self, version: str) -> SecretStr | None:
        if not isinstance(version, str):
            return None
        pepper = self._peppers.get(version)
        if pepper is None or len(pepper.get_secret_value().strip()) < MINIMUM_PEPPER_LENGTH:
            return None
        return pepper
