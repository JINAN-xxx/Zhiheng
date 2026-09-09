"""应用级资源名称；独立于 Hook，避免依赖循环。"""

RUNTIME_CLIENT_STATE_NAME = 'ai_litellm_runtime_client'
MANAGEMENT_CLIENT_STATE_NAME = 'ai_litellm_management_client'
CLIENT_STATE_NAME = RUNTIME_CLIENT_STATE_NAME
KNOWLEDGE_RUNTIME_STATE_NAME = 'knowledge_runtime'
