"""Wire text validation retains native semantics across supported Python runtimes."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from zhiheng.api.resources.schemas.knowledge import CreateRequest, FolderPatchRequest, PatchRequest, QueryBody


def test_knowledge_wire_text_cleaning_none_and_query_whitespace() -> None:
    create = CreateRequest(name='  我的知识库  ', description='  资料说明  ', embed_model_id=uuid4())
    assert create.name == '我的知识库' and create.description == '资料说明'
    assert CreateRequest(name='Valid', description='', embed_model_id=uuid4()).description == ''
    patch = PatchRequest(name='  New name  ', description='  ')
    assert patch.name == 'New name' and patch.description == ''
    nullable = PatchRequest(name=None, description=None)
    assert nullable.name is None and nullable.description is None
    assert nullable.model_fields_set == {'name', 'description'}
    assert PatchRequest().model_fields_set == set()
    assert QueryBody(query='  保留检索空白  ').query == '  保留检索空白  '
    assert FolderPatchRequest(name='  Folder  ').name == 'Folder'


@pytest.mark.parametrize('text', ['  ', 'invalid\x00text', '\ud800'])
def test_create_and_query_reject_invalid_text(text: str) -> None:
    for payload in ({'name': text}, {'name': 'Valid', 'description': text}):
        with pytest.raises(ValidationError):
            CreateRequest(embed_model_id=uuid4(), **payload)
    with pytest.raises(ValidationError):
        QueryBody(query=text)


@pytest.mark.parametrize(
    'payload',
    [
        {'name': '  '},
        {'name': 'invalid\x00text'},
        {'description': 'invalid\x00text'},
        {'name': '\ud800'},
        {'description': '\ud800'},
    ],
)
def test_patch_distinguishes_name_and_description_and_rejects_invalid_text(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        PatchRequest(**payload)
