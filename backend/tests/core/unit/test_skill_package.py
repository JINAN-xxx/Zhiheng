import importlib.util
import io
import stat
import warnings
import zipfile

import pytest


def markdown(slug: str = 'sample', body: str = 'Instruction') -> bytes:
    return f'---\nname: {slug}\ndisplay_name: Sample\ndescription: A test\n---\n{body}'.encode()


def archive(entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    data = io.BytesIO()
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message="Duplicate name: 'SKILL.md'", category=UserWarning)
        with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
            for name, value in entries:
                zipped.writestr(name, value)
    return data.getvalue()


def parser() -> object:
    assert importlib.util.find_spec('zhiheng.modules.skills') is not None, 'Skills package parser is missing'
    from zhiheng.modules.skills.package import SkillPackageParser

    return SkillPackageParser()


def test_plain_and_zip_preserve_content_attachments_and_stable_tree_hash() -> None:
    value = parser()
    raw = markdown()
    plain = value.parse(raw, filename='SKILL.md')
    zipped = value.parse(archive([('SKILL.md', raw)]), filename='skill.zip')
    assert plain.content == raw.decode() and plain.content_hash == zipped.content_hash
    with_attachment = value.parse(archive([('notes.txt', b'kept'), ('SKILL.md', raw)]), filename='skill.zip')
    assert with_attachment.files == (('SKILL.md', raw), ('notes.txt', b'kept'))
    assert with_attachment.content_hash != plain.content_hash
    assert plain.package_version == '1.0.0'


@pytest.mark.parametrize('directory', ['writing-assistant', '技能包', 'export-v2'])
def test_single_directory_zip_uses_same_relative_tree_as_flat_package(directory: str) -> None:
    entries = [
        ('SKILL.md', markdown('writing-assistant').replace(b'\n', b'\r\n')),
        ('agents/openai.yaml', b'interface: {}'),
    ]
    flat = parser().parse(archive(entries), filename='flat.zip')
    wrapped = parser().parse(
        archive([(directory + '/', b''), *[(directory + '/' + name, content) for name, content in entries]]),
        filename='writing-assistant.zip',
    )
    assert wrapped.slug == 'writing-assistant'
    assert wrapped.files == flat.files
    assert wrapped.content == flat.content
    assert wrapped.content_hash == flat.content_hash
    assert wrapped.parsed_hash == flat.parsed_hash


@pytest.mark.parametrize('layout', ['missing', 'multiple', 'mixed', 'nested'])
def test_unsupported_zip_layout_explains_main_document_placement(layout: str) -> None:
    from zhiheng.shared.errors import AppError

    entries = {
        'missing': [('README.md', b'No main file')],
        'multiple': [('one/SKILL.md', markdown('one')), ('two/SKILL.md', markdown('two'))],
        'mixed': [('one/SKILL.md', markdown()), ('README.md', b'Outside skill')],
        'nested': [('export/skill/SKILL.md', markdown())],
    }[layout]
    with pytest.raises(AppError) as error:
        parser().parse(archive(entries), filename='skill.zip')
    assert error.value.code == 'invalid_skill_package'
    assert 'SKILL.md' in error.value.message


@pytest.mark.parametrize('unsafe', ['writing-assistant/../bad', '../outside', 'writing-assistant/./bad'])
def test_wrapper_never_hides_unsafe_original_paths(unsafe: str) -> None:
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError):
        parser().parse(archive([('writing-assistant/SKILL.md', markdown()), (unsafe, b'bad')]), filename='skill.zip')


@pytest.mark.parametrize('violation', ['main_size', 'file_count', 'symlink'])
def test_single_directory_archive_keeps_original_package_limits(violation: str) -> None:
    from zhiheng.shared.errors import AppError

    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = [('folder/SKILL.md', markdown())]
    if violation == 'main_size':
        entries[0] = ('folder/SKILL.md', markdown(body='x' * (128 * 1024)))
    elif violation == 'file_count':
        entries.extend((f'folder/references/{index}.txt', b'') for index in range(200))
    else:
        link = zipfile.ZipInfo('folder/linked')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o600) << 16
        entries.append((link, b'outside'))
    with pytest.raises(AppError) as error:
        parser().parse(archive(entries), filename='folder.zip')
    assert error.value.code == 'invalid_skill_package'


@pytest.mark.parametrize('filename', ['SKILL.md', 'minimal.zip'])
def test_minimal_frontmatter_without_display_name_uses_skill_name(filename: str) -> None:
    raw = b'---\nname: minimal-skill\ndescription: Summarize supplied material\n---\nKeep the original facts.'
    payload = raw if filename == 'SKILL.md' else archive([('SKILL.md', raw), ('references/example.md', b'Context')])
    parsed = parser().parse(payload, filename=filename)
    assert parsed.slug == parsed.name == 'minimal-skill'
    assert parsed.description == 'Summarize supplied material'
    assert parsed.package_version == '1.0.0'
    assert parsed.content == raw.decode()
    assert parsed.file_count == (1 if filename == 'SKILL.md' else 2)


@pytest.mark.parametrize('display_name', [b'null', b'""', b'123', b'"\\0"', b'x' * 101])
def test_explicit_invalid_display_name_does_not_silently_fall_back(display_name: bytes) -> None:
    from zhiheng.shared.errors import AppError

    raw = b'---\nname: sample\ndescription: A test\ndisplay_name: ' + display_name + b'\n---\nInstruction'
    with pytest.raises(AppError) as error:
        parser().parse(raw, filename='SKILL.md')
    assert error.value.code == 'invalid_skill_package'


@pytest.mark.parametrize('path', ['/etc/a', '../a', 'a/../b', 'a\\b', 'a:b', 'a//b', './a'])
def test_unsafe_zip_paths_are_rejected(path: str) -> None:
    value = parser()
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError) as error:
        value.parse(archive([('SKILL.md', markdown()), (path, b'x')]), filename='a.zip')
    assert error.value.code == 'invalid_skill_package'


@pytest.mark.parametrize('kind', ['duplicate', 'case', 'conflict', 'link', 'special', 'files', 'bomb'])
def test_invalid_zip_members_are_rejected(kind: str) -> None:
    value = parser()
    from zhiheng.shared.errors import AppError

    entries: list[tuple[str | zipfile.ZipInfo, bytes]] = [('SKILL.md', markdown())]
    if kind == 'duplicate':
        entries.append(('SKILL.md', markdown()))
    elif kind == 'case':
        entries.append(('skill.md', b'x'))
    elif kind == 'conflict':
        entries.extend([('a', b'x'), ('a/b', b'y')])
    elif kind in {'link', 'special'}:
        info = zipfile.ZipInfo('unsafe')
        info.create_system = 3
        info.external_attr = ((stat.S_IFLNK if kind == 'link' else stat.S_IFIFO) | 0o600) << 16
        entries.append((info, b'x'))
    elif kind == 'files':
        entries.extend((f'{index}.txt', b'') for index in range(200))
    else:
        entries.append(('bomb', b'0' * (30 * 1024 * 1024)))
    with pytest.raises(AppError) as error:
        value.parse(archive(entries), filename='a.zip')
    assert error.value.code == 'invalid_skill_package'


@pytest.mark.parametrize(
    'raw',
    [
        b'\xff',
        b'---\n- a\n---\nbody',
        b'---\nname: [\n---\nbody',
        b'no metadata',
        b'---\nname: Bad\ndisplay_name: A\ndescription: B\n---',
        b'---\nx: &x [*x]\n---',
        b'---\nx: ' + b'[' * 2000 + b'0' + b']' * 2000 + b'\n---',
    ],
)
def test_metadata_errors_have_stable_domain_error(raw: bytes) -> None:
    value = parser()
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError) as error:
        value.parse(raw, filename='SKILL.md')
    assert error.value.code == 'invalid_skill_package'


@pytest.mark.parametrize('entries', [[('A/x', b'x'), ('a/y', b'y')], [('a/B/', b''), ('a/b/c', b'c')]])
def test_implicit_directories_cannot_have_conflicting_case(entries: list[tuple[str, bytes]]) -> None:
    value = parser()
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError):
        value.parse(archive([('SKILL.md', markdown()), *entries]), filename='a.zip')


@pytest.mark.parametrize(
    'payload,filename',
    [
        (b'x' * (10 * 1024 * 1024 + 1), 'SKILL.md'),
        (markdown(body='x' * (128 * 1024)), 'SKILL.md'),
        (markdown(), 'other.md'),
    ],
    ids=['oversized-upload', 'oversized-main-file', 'unsupported-filename'],
)
def test_upload_and_main_file_and_filename_limits(payload: bytes, filename: str) -> None:
    value = parser()
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError):
        value.parse(payload, filename=filename)


def test_zip_encryption_and_corruption_are_stable_errors() -> None:
    from zhiheng.shared.errors import AppError

    value = parser()
    raw = bytearray(archive([('SKILL.md', markdown())]))
    central = raw.index(b'PK\x01\x02')
    raw[6] |= 1
    raw[central + 8] |= 1
    with pytest.raises(AppError):
        value.parse(bytes(raw), filename='encrypted.zip')
    damaged = bytearray(archive([('SKILL.md', markdown())]))
    damaged[damaged.index(b'PK\x01\x02') + 16] ^= 1
    with pytest.raises(AppError):
        value.parse(bytes(damaged), filename='damaged.zip')


def test_no_skill_fingerprint_remains_compatible_with_previous_runs() -> None:
    import hashlib
    import json
    from uuid import uuid4

    from zhiheng.application.chat.runs import ChatRunService

    resource, model = uuid4(), uuid4()
    previous = hashlib.sha256(
        json.dumps(['chat', str(resource), str(model), 'hello'], ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()
    assert ChatRunService.fingerprint('chat', resource, model, 'hello') == previous


def test_tree_hash_distinguishes_file_boundaries() -> None:
    value = parser()
    encoded_b = (1).to_bytes(4, 'big') + b'b'
    encoded_c = (1).to_bytes(4, 'big') + b'c'
    one = value.parse(archive([('SKILL.md', markdown()), ('a', b''), ('b', encoded_c + b'x')]), filename='one.zip')
    two = value.parse(archive([('SKILL.md', markdown()), ('a', encoded_b), ('c', b'x')]), filename='two.zip')
    assert one.file_count == two.file_count and one.total_bytes == two.total_bytes
    assert one.content_hash != two.content_hash


def test_main_content_nul_is_rejected_before_postgresql_text_storage() -> None:
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError) as error:
        parser().parse(markdown(body='instructions\x00hidden'), filename='SKILL.md')
    assert error.value.code == 'invalid_skill_package'


def corrupt_deflate() -> bytes:
    import struct

    payload = bytearray(archive([('SKILL.md', markdown())]))
    name_length, extra_length = struct.unpack_from('<HH', payload, 26)
    payload[30 + name_length + extra_length] = 0xFF
    return bytes(payload)


def test_corrupt_deflate_is_a_stable_package_error() -> None:
    from zhiheng.shared.errors import AppError

    with pytest.raises(AppError) as error:
        parser().parse(corrupt_deflate(), filename='broken.zip')
    assert error.value.code == 'invalid_skill_package'


@pytest.mark.parametrize('compression,delta', [(zipfile.ZIP_BZIP2, 0), (zipfile.ZIP_LZMA, 4)])
def test_corrupt_other_zip_codecs_are_stable_errors(compression: int, delta: int) -> None:
    import struct

    from zhiheng.shared.errors import AppError

    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=compression) as zipped:
        zipped.writestr('SKILL.md', markdown())
    payload = bytearray(data.getvalue())
    name_length, extra_length = struct.unpack_from('<HH', payload, 26)
    payload[30 + name_length + extra_length + delta] = 0xFF
    with pytest.raises(AppError) as error:
        parser().parse(bytes(payload), filename='broken.zip')
    assert error.value.code == 'invalid_skill_package'


def test_directory_entries_cannot_hide_oversized_compressed_data() -> None:
    from zhiheng.shared.errors import AppError

    payload = archive([('SKILL.md', markdown()), ('directory/', b'x' * (31 * 1024 * 1024))])
    with pytest.raises(AppError) as error:
        parser().parse(payload, filename='directory.zip')
    assert error.value.code == 'invalid_skill_package'


@pytest.mark.parametrize('slug', ['nul\x00slug', '\ud800', 'a' * 256, 'Bad', 'under_score', '中文', ''])
def test_chat_skill_slug_contract_rejects_invalid_identifiers(slug: str) -> None:
    from uuid import uuid4

    from pydantic import ValidationError

    from zhiheng.api.console.chat import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(model_id=uuid4(), message='hello', client_request_id=uuid4(), skill_slugs=[slug])


def test_skill_package_slug_length_has_same_selection_boundary() -> None:
    from zhiheng.shared.errors import AppError

    assert parser().parse(markdown(slug='a' * 255), filename='SKILL.md').slug == 'a' * 255
    with pytest.raises(AppError):
        parser().parse(markdown(slug='a' * 256), filename='SKILL.md')
