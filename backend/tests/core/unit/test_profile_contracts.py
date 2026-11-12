import pytest
from pydantic import ValidationError

from zhiheng.modules.identity import public


def test_profile_patch_trims_fields_and_preserves_partial_updates() -> None:
    body = public.ProfilePatch(display_name=' 本人 ', email=' ', expected_version=1)
    assert body.model_dump(exclude_unset=True) == {'display_name': '本人', 'email': '', 'expected_version': 1}


@pytest.mark.parametrize(
    'fields',
    [
        {'display_name': 'x' * 101},
        {'email': 'x' * 255},
        {'phone': 'x' * 41},
        {'display_name': None},
        {'username': 'another'},
        {'expected_version': True},
    ],
)
def test_profile_patch_rejects_invalid_fields(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        public.ProfilePatch.model_validate({'expected_version': 1, **fields})


def test_password_change_preserves_whitespace_and_counts_unicode_characters() -> None:
    password = ' 密码' * 4
    body = public.PasswordChange(current_password=' old ', new_password=password, expected_version=1)
    assert body.current_password.get_secret_value() == ' old '
    assert body.new_password.get_secret_value() == password
    assert password not in str(body)


@pytest.mark.parametrize('password', ['x' * 11, 'x' * 1025])
def test_password_change_enforces_existing_password_length_bounds(password: str) -> None:
    with pytest.raises(ValidationError):
        public.PasswordChange(current_password='current-password', new_password=password, expected_version=1)
