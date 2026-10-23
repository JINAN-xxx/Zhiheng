import base64
import traceback
from collections.abc import Callable

import pytest
from pydantic import SecretStr

from plugins.ai.infrastructure import developer_key_codec
from plugins.ai.infrastructure.developer_key_codec import (
    DeveloperKeyCodec,
    DeveloperKeyCodecError,
    IssuedDeveloperKey,
)

KEY_ID = 'key_123e4567e89b42d3a456426614174000'
PEPPER = 'developer-api-pepper-value-at-least-32-characters'
SECOND_PEPPER = 'second-developer-api-pepper-at-least-32-characters'
TOKEN_SIZE = 32
DIGEST_LENGTH = 64
FIXED_BYTES = bytes(range(TOKEN_SIZE))
EXPECTED_TOKEN = base64.urlsafe_b64encode(FIXED_BYTES).rstrip(b'=').decode('ascii')


def _codec(
    *,
    token_bytes: Callable[[int], bytes] = lambda size: FIXED_BYTES,
    current_version: str = 'v1',
) -> DeveloperKeyCodec:
    return DeveloperKeyCodec(
        current_version=current_version,
        peppers={'v1': SecretStr(PEPPER), 'v2': SecretStr(SECOND_PEPPER)},
        token_bytes=token_bytes,
    )


def _production_traceback_locals(error: BaseException) -> list[dict[str, object]]:
    locals_by_frame: list[dict[str, object]] = []
    current = error.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename
        if filename.replace('\\', '/').endswith('plugins/ai/infrastructure/developer_key_codec.py'):
            locals_by_frame.append(dict(current.tb_frame.f_locals))
        current = current.tb_next
    return locals_by_frame


def test_issue_uses_32_random_bytes_and_base64url_without_padding() -> None:
    requested_sizes: list[int] = []

    def token_bytes(size: int) -> bytes:
        requested_sizes.append(size)
        return FIXED_BYTES

    issued = _codec(token_bytes=token_bytes).issue(KEY_ID)

    assert requested_sizes == [TOKEN_SIZE]
    assert issued.complete_key == f'sk-zh_{KEY_ID}_{EXPECTED_TOKEN}'
    assert '=' not in issued.complete_key
    assert issued.prefix == f'sk-zh_{KEY_ID}'
    assert issued.fingerprint == EXPECTED_TOKEN[-4:]
    assert len(issued.digest) == DIGEST_LENGTH
    assert issued.digest_version == 'v1'


def test_issued_key_repr_excludes_complete_key_digest_and_pepper() -> None:
    issued = _codec().issue(KEY_ID)
    serialized = repr(issued)

    assert issued.complete_key not in serialized
    assert issued.digest not in serialized
    assert PEPPER not in serialized
    assert 'fingerprint' in serialized


def test_issue_returns_frozen_slotted_result() -> None:
    issued = _codec().issue(KEY_ID)

    assert isinstance(issued, IssuedDeveloperKey)
    assert not hasattr(issued, '__dict__')
    with pytest.raises((AttributeError, TypeError)):
        issued.fingerprint = 'changed'  # type: ignore[misc]


def test_issue_and_verify_use_versioned_pepper() -> None:
    v1 = _codec(current_version='v1').issue(KEY_ID)
    v2 = _codec(current_version='v2').issue(KEY_ID)
    codec = _codec()

    assert v1.digest != v2.digest
    assert codec.verify(v1.complete_key, version='v1', expected_digest=v1.digest) is True
    assert codec.verify(v1.complete_key, version='v2', expected_digest=v1.digest) is False
    assert codec.verify(v2.complete_key, version='v2', expected_digest=v2.digest) is True


@pytest.mark.parametrize(
    ('complete_key', 'version', 'digest'),
    (
        ('', 'v1', '0' * 64),
        ('sk-zh-invalid', 'v1', '0' * 64),
        (f'sk-zh_{KEY_ID}_{EXPECTED_TOKEN}=', 'v1', '0' * 64),
        (f'sk-zh_{KEY_ID}_{EXPECTED_TOKEN}', 'unknown', '0' * 64),
        (f'sk-zh_{KEY_ID}_{EXPECTED_TOKEN}', 'v1', 'short'),
        (f'sk-zh_{KEY_ID}_{EXPECTED_TOKEN}', 'v1', 'g' * 64),
    ),
)
def test_verify_rejects_invalid_input_without_raising(complete_key: str, version: str, digest: str) -> None:
    assert _codec().verify(complete_key, version=version, expected_digest=digest) is False


def test_verify_calls_constant_time_digest_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    issued = _codec().issue(KEY_ID)
    comparisons: list[tuple[str, str]] = []

    def compare_digest(actual: str, expected: str) -> bool:
        comparisons.append((actual, expected))
        return actual == expected

    monkeypatch.setattr(developer_key_codec.hmac, 'compare_digest', compare_digest)

    assert _codec().verify(issued.complete_key, version='v1', expected_digest=issued.digest) is True
    assert comparisons == [(issued.digest, issued.digest)]


def test_mask_only_exposes_short_prefix_and_fingerprint() -> None:
    masked = _codec().mask(f'sk-zh_{KEY_ID}', '9f3c')

    assert masked.startswith('sk-zh_key_')
    assert masked.endswith('...9f3c')
    assert KEY_ID not in masked


@pytest.mark.parametrize('key_id', ('', 'mdl_123e4567e89b42d3a456426614174000', 'key_not-a-uuid'))
def test_issue_rejects_invalid_key_id_before_random_generation(key_id: str) -> None:
    random_called = False

    def token_bytes(size: int) -> bytes:
        nonlocal random_called
        random_called = True
        return b'x' * size

    with pytest.raises(ValueError, match='资源 ID'):
        _codec(token_bytes=token_bytes).issue(key_id)

    assert random_called is False


@pytest.mark.parametrize('returned', (b'', b'x' * 31, b'x' * 33, 'x' * 32))
def test_issue_rejects_invalid_random_source_output_without_exposing_material(returned: object) -> None:
    with pytest.raises(DeveloperKeyCodecError) as raised:
        _codec(token_bytes=lambda size: returned).issue(KEY_ID)  # type: ignore[arg-type,return-value]

    assert raised.value.args == ()
    assert returned not in raised.value.args


def test_issue_crypto_failure_raises_clean_error_without_sensitive_traceback_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_complete_key = f'sk-zh_{KEY_ID}_{EXPECTED_TOKEN}'

    def fail_hmac(*args: object, **kwargs: object) -> object:
        raise RuntimeError(f'private failure containing {expected_complete_key}')

    monkeypatch.setattr(developer_key_codec.hmac, 'new', fail_hmac)

    with pytest.raises(DeveloperKeyCodecError) as raised:
        _codec().issue(KEY_ID)

    error = raised.value
    serialized = repr((str(error), repr(error), error.args, error.__cause__, error.__context__))
    frame_locals = repr(_production_traceback_locals(error))
    formatted = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    assert expected_complete_key not in serialized
    assert expected_complete_key not in frame_locals
    assert expected_complete_key not in formatted
    assert error.__cause__ is None
    assert error.__context__ is None


def test_codec_rejects_unknown_current_version_and_weak_pepper_without_secret_in_error() -> None:
    for codec in (
        DeveloperKeyCodec(current_version='v3', peppers={'v1': SecretStr(PEPPER)}),
        DeveloperKeyCodec(current_version='v1', peppers={'v1': SecretStr('short')}),
    ):
        with pytest.raises(DeveloperKeyCodecError) as raised:
            codec.issue(KEY_ID)

        assert PEPPER not in repr(raised.value)
        assert 'short' not in repr(raised.value)
