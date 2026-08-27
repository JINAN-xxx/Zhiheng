function trimSlashes(value) {
  return String(value || '').replace(/^\/+|\/+$/g, '')
}

export function buildPublicApiBase(origin, baseApi) {
  const normalizedOrigin = String(origin || '').replace(/\/+$/, '')
  const path = trimSlashes(baseApi)
  return `${normalizedOrigin}${path ? `/${path}` : ''}/v1`
}

const normalChatRequest = `{
  "model": "<MODEL_CODE>",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "stream": false
}`

const streamChatRequest = `{
  "model": "<MODEL_CODE>",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "stream": true
}`

const pythonHeaders = `headers = {
    "Authorization": "Bearer <DEVELOPER_API_KEY>",
    "Content-Type": "application/json",
}`

const completionId = 'chatcmpl_0123456789abcdef0123456789abcdef'
const created = 1787587200

export const chatStreamFrames = [
  { id: completionId, object: 'chat.completion.chunk', created, model: '<MODEL_CODE>', choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] },
  { id: completionId, object: 'chat.completion.chunk', created, model: '<MODEL_CODE>', choices: [{ index: 0, delta: { content: '你好' }, finish_reason: null }] },
  { id: completionId, object: 'chat.completion.chunk', created, model: '<MODEL_CODE>', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] },
  '[DONE]',
]

export const publicApiErrors = [
  { status: 404, code: 'not_found', type: 'invalid_request_error', description: '公开 API 入口已关闭或请求资源不存在。' },
  { status: 401, code: 'invalid_api_key', type: 'authentication_error', description: '访问密钥无效、已撤销或所属应用已停用。' },
  { status: 403, code: 'permission_denied', type: 'permission_error', description: '当前密钥没有目标模型权限。' },
  { status: 404, code: 'model_not_found', type: 'invalid_request_error', description: '模型不存在或未发布。' },
  { status: 400, code: 'invalid_request', type: 'invalid_request_error', description: '请求结构或字段不符合协议。' },
  { status: 502, code: 'upstream_error', type: 'api_error', description: '模型服务提供方请求失败。' },
  { status: 503, code: 'service_unavailable', type: 'api_error', description: '模型服务暂时不可用。' },
  { status: 413, code: 'request_too_large', type: 'invalid_request_error', description: '请求正文超过平台允许的大小。' },
  { status: 429, code: 'rate_limit_exceeded', type: 'rate_limit_error', description: '超过当前每分钟请求或令牌限额。' },
  { status: 429, code: 'concurrency_limit_exceeded', type: 'rate_limit_error', description: '超过当前并发调用限额。' },
  { status: 429, code: 'budget_exceeded', type: 'rate_limit_error', description: '达到当前月度预算限额。' },
]

export const apiDocNavigation = [
  { anchor: 'api-overview', label: '接入说明' },
  { anchor: 'api-auth', label: '鉴权方式' },
  { anchor: 'api-models', label: '查询可用模型', method: 'GET' },
  { anchor: 'api-chat-completions', label: '对话生成', method: 'POST' },
  { anchor: 'api-embeddings', label: '向量生成', method: 'POST' },
  { anchor: 'api-rerank', label: '文本排序', method: 'POST' },
  { anchor: 'api-streaming', label: '流式调用' },
  { anchor: 'api-errors', label: '错误码' },
]

export const apiDocs = [
  {
    id: 'models',
    method: 'GET',
    path: '/v1/models',
    title: '查询可用模型',
    description: '返回当前访问密钥有权限调用的平台模型。',
    fields: [{ name: 'Authorization', type: '请求头', required: true, description: 'Bearer <DEVELOPER_API_KEY>' }],
    requestExample: '',
    responseFields: [
      { name: 'object', type: 'string', description: '固定为 list。' },
      { name: 'data[].id', type: 'string', description: '可调用的模型编码。' },
      { name: 'data[].created', type: 'integer', description: '模型发布时间戳。' },
    ],
    curlExample: 'curl "{{API_BASE}}/models" \\\n  -H "Authorization: Bearer <DEVELOPER_API_KEY>"',
    pythonExample: `import requests

${pythonHeaders}
response = requests.get("{{API_BASE}}/models", headers=headers)
response.raise_for_status()
print(response.json())`,
    responseExample: '{\n  "object": "list",\n  "data": [{"id": "<MODEL_CODE>", "object": "model", "created": 0, "owned_by": "zhiheng"}]\n}',
  },
  {
    id: 'chat-completions',
    method: 'POST',
    path: '/v1/chat/completions',
    title: '对话生成',
    description: '兼容 OpenAI Chat Completions。设置 stream=true 时返回 SSE 流式响应。',
    streaming: true,
    fields: [
      { name: 'model', type: 'string', required: true, description: '模型编码。' },
      { name: 'messages', type: 'array', required: true, description: '按顺序传入 system、developer、user、assistant 消息。' },
      { name: 'stream', type: 'boolean', required: false, description: '是否流式返回，默认 false。' },
      { name: 'temperature / top_p', type: 'number', required: false, description: '采样参数。' },
      { name: 'max_tokens', type: 'integer', required: false, description: '最大输出令牌数。' },
      { name: 'presence_penalty', type: 'number', required: false, description: '主题重复惩罚，范围 -2 到 2。' },
      { name: 'frequency_penalty', type: 'number', required: false, description: '词频重复惩罚，范围 -2 到 2。' },
      { name: 'stop', type: 'string | array', required: false, description: '停止序列，最多四个。' },
      { name: 'user', type: 'string', required: false, description: '调用方标识。' },
    ],
    normalRequestExample: normalChatRequest,
    streamRequestExample: streamChatRequest,
    requestExample: normalChatRequest,
    normalResponseFields: [
      { name: 'id', type: 'string', description: '本次对话请求标识。' },
      { name: 'object', type: 'string', description: '固定为 chat.completion。' },
      { name: 'created', type: 'integer', description: '响应创建时间戳。' },
      { name: 'model', type: 'string', description: '实际调用的模型编码。' },
      { name: 'choices[].message', type: 'object', description: '助手回复，包含 role 和 content。' },
      { name: 'choices[].finish_reason', type: 'string', description: '结束原因：stop 或 length。' },
      { name: 'usage', type: 'object', description: '输入、输出和总令牌用量。' },
    ],
    streamResponseFields: [
      { name: 'id', type: 'string', description: '本次对话请求标识。' },
      { name: 'object', type: 'string', description: '固定为 chat.completion.chunk。' },
      { name: 'created', type: 'integer', description: '响应创建时间戳。' },
      { name: 'model', type: 'string', description: '实际调用的模型编码。' },
      { name: 'choices[].delta', type: 'object', description: '角色、文本或空对象增量。' },
      { name: 'choices[].finish_reason', type: 'string | null', description: '内容帧为 null，完成帧为 stop 或 length。' },
    ],
    normalResponseExample: `{
  "id": "${completionId}",
  "object": "chat.completion",
  "created": ${created},
  "model": "<MODEL_CODE>",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好"}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
}`,
    streamResponseExample: chatStreamFrames.map((frame) => `data: ${typeof frame === 'string' ? frame : JSON.stringify(frame)}`).join('\n\n'),
    curlExample: `curl "{{API_BASE}}/chat/completions" \\
  -H "Authorization: Bearer <DEVELOPER_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '${streamChatRequest.replace(/\n/g, '')}'`,
    pythonExample: `import requests

${pythonHeaders}
payload = ${streamChatRequest.replace(/\btrue\b/, 'True')}
with requests.post("{{API_BASE}}/chat/completions", headers=headers, json=payload, stream=True) as response:
    response.raise_for_status()
    for line in response.iter_lines(decode_unicode=True):
        if line:
            print(line)`,
    responseExample: `{
  "id": "${completionId}",
  "object": "chat.completion",
  "created": ${created},
  "model": "<MODEL_CODE>",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好"}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
}`,
  },
  {
    id: 'embeddings',
    method: 'POST',
    path: '/v1/embeddings',
    title: '向量生成',
    description: '将一条或多条文本转换为向量。单次最多 256 条输入。',
    fields: [
      { name: 'model', type: 'string', required: true, description: '向量模型编码。' },
      { name: 'input', type: 'string | array', required: true, description: '待转换文本或文本数组。' },
      { name: 'encoding_format', type: 'string', required: false, description: '目前支持 float。' },
      { name: 'dimensions', type: 'integer', required: false, description: '返回向量维度。' },
      { name: 'user', type: 'string', required: false, description: '调用方标识。' },
    ],
    requestExample: '{\n  "model": "<MODEL_CODE>",\n  "input": ["第一段文本", "第二段文本"]\n}',
    responseFields: [
      { name: 'data[].embedding', type: 'array', description: '浮点数向量。' },
      { name: 'data[].index', type: 'integer', description: '对应输入文本的序号。' },
      { name: 'usage', type: 'object', description: '输入令牌用量。' },
    ],
    curlExample: `curl "{{API_BASE}}/embeddings" \\
  -H "Authorization: Bearer <DEVELOPER_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"<MODEL_CODE>","input":["第一段文本","第二段文本"]}'`,
    pythonExample: `import requests

${pythonHeaders}
payload = {"model": "<MODEL_CODE>", "input": ["第一段文本", "第二段文本"]}
response = requests.post("{{API_BASE}}/embeddings", headers=headers, json=payload)
response.raise_for_status()
print(response.json())`,
    responseExample: '{\n  "object": "list",\n  "data": [{"object": "embedding", "index": 0, "embedding": [0.01, -0.02]}],\n  "model": "<MODEL_CODE>",\n  "usage": {"prompt_tokens": 4, "total_tokens": 4}\n}',
  },
  {
    id: 'rerank',
    method: 'POST',
    path: '/v1/rerank',
    title: '文本排序',
    description: '根据查询文本对候选文档进行相关性排序，兼容 LiteLLM/Cohere 格式。',
    fields: [
      { name: 'model', type: 'string', required: true, description: '排序模型编码。' },
      { name: 'query', type: 'string', required: true, description: '查询文本。' },
      { name: 'documents', type: 'array', required: true, description: '候选文档文本数组，最多 256 条。' },
      { name: 'top_n', type: 'integer', required: false, description: '返回结果数量。' },
      { name: 'return_documents', type: 'boolean', required: false, description: '是否在结果中返回文档内容。' },
    ],
    requestExample: '{\n  "model": "<MODEL_CODE>",\n  "query": "用户问题",\n  "documents": ["候选文档一", "候选文档二"],\n  "top_n": 2,\n  "return_documents": true\n}',
    responseFields: [
      { name: 'results[].index', type: 'integer', description: '原始候选文档序号。' },
      { name: 'results[].relevance_score', type: 'number', description: '相关性分数。' },
      { name: 'results[].document', type: 'object | null', description: '请求返回文档时包含正文。' },
      { name: 'meta', type: 'object', description: '计费用量和令牌信息。' },
    ],
    curlExample: `curl "{{API_BASE}}/rerank" \\
  -H "Authorization: Bearer <DEVELOPER_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"<MODEL_CODE>","query":"用户问题","documents":["候选一","候选二"],"top_n":2}'`,
    pythonExample: `import requests

${pythonHeaders}
payload = {"model": "<MODEL_CODE>", "query": "用户问题", "documents": ["候选一", "候选二"], "top_n": 2}
response = requests.post("{{API_BASE}}/rerank", headers=headers, json=payload)
response.raise_for_status()
print(response.json())`,
    responseExample: '{\n  "id": "rerank_<id>",\n  "results": [{"index": 0, "relevance_score": 0.98, "document": {"text": "候选文档一"}}],\n  "meta": {"billed_units": {}, "tokens": {}}\n}',
  },
]
