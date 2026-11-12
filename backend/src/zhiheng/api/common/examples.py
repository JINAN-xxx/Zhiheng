"""Copyable examples checked against the same registered request DTOs as OpenAPI."""

import json
from typing import Any

from fastapi.routing import APIRoute, RouteContext
from pydantic import BaseModel

MODEL = '00000000-0000-4000-8000-000000000001'
FILE = '00000000-0000-4000-8000-000000000002'

# These are recipe data, not a second endpoint/schema inventory. The generator rejects
# a missing endpoint or a payload that no longer validates against its real DTO.
EXAMPLE_BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ('knowledge', 'create'): {'name': '个人知识库', 'embed_model_id': MODEL},
    ('knowledge', 'patch'): {'name': '更新名称'},
    ('knowledge', 'create_folder'): {'name': '资料'},
    ('knowledge', 'patch_folder'): {'name': '更新目录'},
    ('knowledge', 'import_documents'): {'file_ids': [FILE]},
    ('knowledge', 'query'): {'query': '资料中的主要结论是什么？'},
    ('knowledge', 'rechunk'): {'chunk_size': 1000, 'chunk_overlap': 200},
    ('skills', 'prepare'): {'file_id': FILE},
    ('skills', 'status'): {'status': 'disabled'},
    ('chat', 'chat'): {'model': MODEL, 'messages': [{'role': 'user', 'content': '你好'}], 'stream': True},
    ('embeddings', 'embeddings'): {'model': MODEL, 'input': ['资料文本']},
    ('rerank', 'rerank'): {'model': MODEL, 'query': '问题', 'documents': ['候选文本']},
    ('agents', 'chat'): {'messages': [{'role': 'user', 'content': '你好'}], 'stream': True},
    ('agents', 'cancel_preparation'): {},
}

TITLES = {
    ('files', 'upload'): '上传单个文件',
    ('files', 'page'): '文件列表',
    ('files', 'get'): '文件元数据',
    ('files', 'content'): '下载文件',
    ('files', 'remove'): '回收文件',
    ('files', 'restore'): '恢复文件',
    ('knowledge', 'create'): '创建知识库',
    ('knowledge', 'page'): '知识库列表',
    ('knowledge', 'get'): '知识库详情',
    ('knowledge', 'patch'): '修改知识库',
    ('knowledge', 'folders'): '目录列表',
    ('knowledge', 'create_folder'): '创建目录',
    ('knowledge', 'patch_folder'): '修改目录',
    ('knowledge', 'documents'): '文档列表',
    ('knowledge', 'import_documents'): '文件入库',
    ('knowledge', 'query'): '知识检索',
    ('knowledge', 'remove_base'): '删除知识库',
    ('knowledge', 'rebuild'): '重建知识索引',
    ('knowledge', 'rechunk'): '重新切片文档',
    ('knowledge', 'remove_document'): '删除知识文档',
    ('knowledge', 'download'): '下载知识文档原文件',
    ('knowledge', 'preview'): '预览知识文档',
    ('knowledge', 'remove_folder'): '删除知识目录',
    ('skills', 'prepare'): '预检个人技能',
    ('skills', 'confirm'): '确认技能版本',
    ('skills', 'page'): '个人技能列表',
    ('skills', 'get'): '个人技能详情',
    ('skills', 'status'): '启停个人技能',
    ('skills', 'remove'): '删除个人技能',
    ('skills', 'versions'): '不可变修订列表',
    ('skills', 'revision'): '不可变修订详情',
    ('skills', 'content'): '下载修订原始包',
    ('operations', 'page'): '操作列表',
    ('operations', 'get'): '查询操作',
    ('operations', 'cancel'): '请求取消操作',
    ('models', 'models'): '查询允许的模型',
    ('chat', 'chat'): '对话生成',
    ('embeddings', 'embeddings'): '向量生成',
    ('rerank', 'rerank'): '文本排序',
    ('agents', 'chat'): '调用智能体',
    ('agents', 'preparation'): '智能体准备状态',
    ('agents', 'cancel_preparation'): '取消智能体准备',
}


def _agent_request_example(sample: str) -> str:
    sample = (
        '# 同一请求及续调复用 REQUEST_ID；新问题须生成新 UUID。\n'
        "REQUEST_ID=${REQUEST_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}\n" + sample
    )
    return sample


def add_examples(document: dict[str, Any], routes: list[RouteContext]) -> None:
    for context in routes:
        route = context.original_route
        path_template = context.path_format
        if (
            not isinstance(route, APIRoute)
            or not route.include_in_schema
            or not path_template
            or not path_template.startswith(('/api/v1/', '/v1/'))
        ):
            continue
        key = (route.endpoint.__module__.rsplit('.', 1)[-1], route.endpoint.__name__)
        body = EXAMPLE_BODIES.get(key)
        if body is not None and (
            route.body_field is not None
            and isinstance(route.body_field.field_info.annotation, type)
            and issubclass(route.body_field.field_info.annotation, BaseModel)
        ):
            route.body_field.field_info.annotation.model_validate(body)
        for method in context.methods or set():
            operation = document['paths'][path_template][method.lower()]
            operation['summary'] = TITLES.get(key, operation['summary'])
            path = path_template
            for parameter in operation.get('parameters', []):
                if parameter['in'] == 'path':
                    name = parameter['name']
                    path = path.replace('{' + name + '}', '${' + name.upper() + '}')
            lines = [
                f'curl --fail-with-body -X {method} "{{{{ORIGIN}}}}{path}"',
                '  -H "Authorization: Bearer $API_KEY"',
            ]
            parameters = operation.get('parameters', [])
            if key == ('agents', 'chat'):
                lines.append('  -H "Idempotency-Key: $REQUEST_ID"')
            if any(p['name'] == 'If-Match' for p in parameters):
                lines.append('  -H "If-Match: $ETAG"')
            if any(p['name'] == 'Range' for p in parameters):
                lines.append('  -H "Range: bytes=0-1023"')
            if operation.get('x-transport-policy') == 'multipart_file':
                lines.extend(['  -F "file=@${FILE_PATH}"', '  -F "purpose=knowledge"'])
            elif body is not None:
                encoded = (
                    json.dumps(body, ensure_ascii=False, indent=2)
                    .replace(MODEL, '${EMBED_MODEL_ID}' if key == ('knowledge', 'create') else '${MODEL_ID}')
                    .replace(FILE, '${FILE_ID}')
                )
                lines.append('  -H "Content-Type: application/json"')
                lines.append('  --data @- <<JSON\n' + encoded + '\nJSON')
                content = operation.get('requestBody', {}).get('content', {}).get('application/json')
                if content is not None:
                    content['example'] = body
            sample = ' \\\n'.join(lines)
            sample = _agent_request_example(sample) if key == ('agents', 'chat') else sample
            operation['x-codeSamples'] = [{'lang': 'Shell', 'label': 'curl', 'source': sample}]
    # A filtered document only needs examples for its own real route group. Full coverage
    # is asserted by the route inventory test, rather than inventing placeholder routes.
