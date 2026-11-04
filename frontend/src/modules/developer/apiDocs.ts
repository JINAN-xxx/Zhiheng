import { list, object, string } from '../../shared/validation.ts'

export interface ApiField {
  name: string
  type: string
  required?: boolean
  description: string
}
export interface ApiDocument {
  id: string
  method: string
  path: string
  title: string
  description: string
  scopes: string[]
  scopeMode: string
  fields: ApiField[]
  responseFields: ApiField[]
  curlExample: string
  codeSamples: { label: string; source: string }[]
  requestExample: string
  responses: string
}
function schemaType(schema: Record<string, unknown>): string {
  if (typeof schema.$ref === 'string') return schema.$ref.split('/').at(-1) ?? 'object'
  if (Array.isArray(schema.anyOf))
    return schema.anyOf.map((value) => schemaType(object(value))).join(' | ')
  return typeof schema.type === 'string' ? schema.type : 'object'
}
export function parseOpenApiDocuments(value: unknown): ApiDocument[] {
  const document = object(value)
  if (document.openapi !== '3.1.0') throw new Error('接口规范版本不正确')
  const paths = object(document.paths)
  const components = document.components ? object(document.components) : {}
  const schemas = components.schemas ? object(components.schemas) : {}
  function resolve(value: unknown): Record<string, unknown> {
    const schema = object(value)
    return typeof schema.$ref === 'string'
      ? object(schemas[schema.$ref.split('/').at(-1) ?? ''])
      : schema
  }
  function fields(value: unknown): ApiField[] {
    if (!value) return []
    const schema = resolve(value),
      required = Array.isArray(schema.required) ? schema.required : []
    return Object.entries(schema.properties ? object(schema.properties) : {}).map(
      ([name, value]) => {
        const property = object(value)
        const limits = ['minimum', 'maximum', 'minLength', 'maxLength', 'default', 'enum']
          .filter((key) => property[key] !== undefined)
          .map((key) => `${key}: ${JSON.stringify(property[key])}`)
          .join('；')
        return {
          name,
          type: schemaType(property),
          required: required.includes(name),
          description: [property.description, limits].filter(Boolean).join('；')
        }
      }
    )
  }
  const result: ApiDocument[] = []
  for (const [path, methods] of Object.entries(paths))
    for (const [method, value] of Object.entries(object(methods))) {
      if (!['get', 'post', 'put', 'patch', 'delete'].includes(method)) continue
      const operation = object(value),
        responses = object(operation.responses)
      const request = operation.requestBody ? object(operation.requestBody) : {}
      const content = request.content ? object(request.content) : {}
      const media = Object.values(content)[0],
        body = media ? object(media) : {}
      const response = Object.entries(responses).find(([status]) => status.startsWith('2'))?.[1]
      const responseContent =
        response && object(response).content ? object(object(response).content) : {}
      const responseMedia = Object.values(responseContent)[0]
      const parameters = operation.parameters
        ? list(object)(operation.parameters).map((item) => ({
            name: string(item.name),
            type: `${String(item.in)} · ${schemaType(object(item.schema))}`,
            required: item.required === true,
            description: typeof item.description === 'string' ? item.description : ''
          }))
        : []
      const samples = operation['x-codeSamples'] ? list(object)(operation['x-codeSamples']) : []
      const codeSamples = samples.length
        ? samples.map((sample) => ({
            label: typeof sample.label === 'string' ? sample.label : 'curl',
            source: string(sample.source)
          }))
        : [
            {
              label: 'curl',
              source: `curl --fail-with-body -X ${method.toUpperCase()} "{{ORIGIN}}${path}" -H "Authorization: Bearer $API_KEY"`
            }
          ]
      result.push({
        id: string(operation.operationId),
        method: method.toUpperCase(),
        path,
        title: typeof operation.summary === 'string' ? operation.summary : path,
        description: typeof operation.description === 'string' ? operation.description : '',
        scopes: operation['x-required-scopes'] ? list(string)(operation['x-required-scopes']) : [],
        scopeMode:
          operation['x-required-scopes-mode'] === 'by-kind'
            ? JSON.stringify(operation['x-required-scopes-by-kind'])
            : '',
        fields: [...parameters, ...fields(body.schema)],
        responseFields: responseMedia ? fields(object(responseMedia).schema) : [],
        requestExample: body.example ? JSON.stringify(body.example, null, 2) : '',
        curlExample: codeSamples[0]!.source,
        codeSamples,
        responses: Object.entries(responses)
          .map(([status, value]) => {
            const response = object(value)
            return `${status} ${String(response.description)}${response.headers ? ` · ${Object.keys(object(response.headers)).join(', ')}` : ''}`
          })
          .join('\n')
      })
    }
  return result
}
export async function loadApiDocuments(
  group: 'resources' | 'inference',
  signal: AbortSignal
): Promise<ApiDocument[]> {
  const response = await fetch(`/openapi/${group}-v1.json`, {
    signal,
    credentials: 'omit',
    headers: { Accept: 'application/json' }
  })
  if (!response.ok || !response.headers.get('content-type')?.includes('application/json'))
    throw new Error('接口规范暂时无法加载，请重试')
  return parseOpenApiDocuments(await response.json())
}
export function parseApiDocAnchor(hash: string, anchors: string[]): string {
  let anchor = hash.replace(/^#/, '')
  try {
    anchor = decodeURIComponent(anchor)
  } catch {
    return 'api-overview'
  }
  return anchors.includes(anchor) ? anchor : 'api-overview'
}
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
