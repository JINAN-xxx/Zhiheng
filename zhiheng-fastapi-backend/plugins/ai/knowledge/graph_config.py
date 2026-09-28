"""图谱专用模型策略及可恢复构建的配置指纹。"""

import hashlib
import json
import re
from typing import Any

from plugins.ai.knowledge.models import GraphConfiguration

GRAPH_PIPELINE_VERSION = 'lightrag-1.5.7-chinese-v1'


def graph_parameters(config: GraphConfiguration, resolved: Any) -> dict:
    """仅为已经验证的模型族自动选用抽取参数，未知模型保留默认协议。"""
    strategy = config.reasoning_strategy
    if strategy == 'auto':
        code = str(getattr(resolved, 'model_code', None) or resolved.engine_model_snapshot).lower()
        normalized = re.sub(r'[^a-z0-9]+', '-', code)
        if re.search(r'(?:^|-)glm-5-3(?:-|$)', normalized):
            strategy = 'low'
        elif re.search(r'(?:^|-)deepseek-v4-flash(?:-|$)', normalized):
            strategy = 'disabled'
    parameters = {'max_tokens': config.max_output_tokens}
    if strategy == 'low':
        parameters['reasoning_effort'] = 'low'
    elif strategy == 'disabled':
        parameters['thinking_mode'] = 'disabled'
    return parameters


def model_snapshot(resolved: Any) -> dict:
    """保存缓存隔离所需的绑定事实，不包含凭据。"""
    return {
        'platform_model_id': resolved.platform_model_id,
        'binding_id': resolved.binding_id,
        'engine_model_snapshot': resolved.engine_model_snapshot,
        'model_code': getattr(resolved, 'model_code', None),
        'default_parameters': dict(resolved.default_parameters or {}),
    }


def graph_fingerprint(config: GraphConfiguration, resolved: Any, database: dict) -> str:
    value = {
        'config': config.model_dump(),
        'model': model_snapshot(resolved),
        'chunk_size': database['chunk_size'],
        'chunk_overlap': database['chunk_overlap'],
        'pipeline': GRAPH_PIPELINE_VERSION,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def database_graph_config(database: dict) -> GraphConfiguration | None:
    value = database.get('graph_config')
    if value:
        return GraphConfiguration.model_validate(value)
    model_id = database.get('llm_model_id')
    return GraphConfiguration(llm_model_id=model_id) if model_id else None
