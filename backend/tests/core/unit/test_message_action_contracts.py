from uuid import uuid4

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize('value', ['up', 'down', None])
def test_feedback_contract_accepts_absolute_values(value: str | None) -> None:
    from zhiheng.modules.chat.actions.contracts import FeedbackRequest

    assert FeedbackRequest.model_validate({'value': value}).value == value


@pytest.mark.parametrize('body', [{}, {'value': 'like'}, {'value': True}, {'value': 'up', 'owner_id': str(uuid4())}])
def test_feedback_contract_rejects_invalid_or_extra_fields(body: dict[str, object]) -> None:
    from zhiheng.modules.chat.actions.contracts import FeedbackRequest

    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(body)


def test_share_creation_requires_explicit_preview_hash_and_bounds_expiry() -> None:
    from zhiheng.modules.chat.actions.contracts import ShareCreateRequest

    request = ShareCreateRequest(expected_content_hash='a' * 64)
    assert request.include_question is True and request.expires_in_days == 7
    for fields in (
        {},
        {'expected_content_hash': 'a' * 63},
        {'expected_content_hash': 'z' * 64},
        {'expected_content_hash': 'a' * 64, 'expires_in_days': 0},
        {'expected_content_hash': 'a' * 64, 'expires_in_days': 31},
        {'expected_content_hash': 'a' * 64, 'expires_in_days': True},
        {'expected_content_hash': 'a' * 64, 'include_question': 'false'},
        {'expected_content_hash': 'a' * 64, 'token': 'chosen-by-caller'},
    ):
        with pytest.raises(ValidationError):
            ShareCreateRequest.model_validate(fields)
