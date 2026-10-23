"""Stable graph build diagnostics without model text, credentials or raw exceptions."""

import asyncio

from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.knowledge.models import KnowledgeError

GRAPH_ERROR_MESSAGES = {
    'KNOWLEDGE_GRAPH_MODEL_CHANGED': '图谱模型绑定已变化，请使用当前配置重新构建图谱',
    'KNOWLEDGE_GRAPH_OUTPUT_TRUNCATED': '图谱抽取结果超过单次输出上限，请减小文档切片或调整抽取模型后重试',
    'KNOWLEDGE_GRAPH_LLM_TIMEOUT': '图谱实体抽取请求超时，请稍后重试',
    'KNOWLEDGE_GRAPH_EMBEDDING_TIMEOUT': '图谱向量生成请求超时，请稍后重试',
    'KNOWLEDGE_GRAPH_LLM_FAILED': '图谱抽取模型调用失败，请检查模型配置后重试',
    'KNOWLEDGE_GRAPH_EMBEDDING_FAILED': '图谱向量模型调用失败或返回结果不完整，请检查模型配置后重试',
    'KNOWLEDGE_GRAPH_MODEL_AUTH_FAILED': '图谱使用的模型认证失败，请检查服务商凭据',
    'KNOWLEDGE_GRAPH_MODEL_RATE_LIMITED': '图谱使用的模型请求受限，请稍后重试',
    'KNOWLEDGE_GRAPH_MODEL_UNAVAILABLE': '图谱使用的模型当前不可用，请检查模型发布状态和服务连接',
    'KNOWLEDGE_GRAPH_EMPTY_RESPONSE': '图谱抽取模型未返回可用内容，请检查模型输出设置后重试',
}


def graph_model_error(error: Exception, operation: str) -> KnowledgeError:
    model_code = error.code if isinstance(error, ModelServiceError) else None
    if isinstance(error, asyncio.TimeoutError) or model_code == 'MODEL_RUNTIME_TIMEOUT':
        code = f'KNOWLEDGE_GRAPH_{operation}_TIMEOUT'
    elif model_code in {'MODEL_RUNTIME_AUTH_FAILED', 'MODEL_PROVIDER_AUTH_FAILED'}:
        code = 'KNOWLEDGE_GRAPH_MODEL_AUTH_FAILED'
    elif model_code == 'MODEL_PROVIDER_RATE_LIMITED':
        code = 'KNOWLEDGE_GRAPH_MODEL_RATE_LIMITED'
    elif model_code in {
        'MODEL_NOT_FOUND',
        'MODEL_DISABLED',
        'MODEL_BINDING_MISSING',
        'MODEL_RUNTIME_MISCONFIGURED',
        'MODEL_RUNTIME_UNAVAILABLE',
    }:
        code = 'KNOWLEDGE_GRAPH_MODEL_UNAVAILABLE'
    else:
        code = f'KNOWLEDGE_GRAPH_{operation}_FAILED'
    return KnowledgeError(code, GRAPH_ERROR_MESSAGES[code], 503)


def safe_graph_error(error: Exception) -> KnowledgeError | None:
    if isinstance(error, KnowledgeError) and error.code in GRAPH_ERROR_MESSAGES:
        return KnowledgeError(error.code, GRAPH_ERROR_MESSAGES[error.code], 503)
    if isinstance(error, KnowledgeError) and error.code == 'KNOWLEDGE_REINDEX_REQUIRED':
        return KnowledgeError.reindex()
    return None
