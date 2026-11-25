import { http } from '../../../shared/http.ts'
import { boolean, number, object, string } from '../../../shared/validation.ts'

const settingsPath = '/knowledge/settings/parsing'

interface DocumentParsingUpdate {
  expectedVersion: number
  localUrl: string
  localApiKey: string
  cloudEnabled: boolean
  cloudUrl: string
  cloudApiKey: string
  tier: string
  timeoutSeconds: number
  queueTimeoutSeconds: number
}

function parseSettings(value: unknown) {
  const data = object(value)
  return {
    version: number(data.version),
    localUrl: data.local_url == null ? '' : string(data.local_url),
    hasLocalKey: boolean(data.has_local_key),
    cloudEnabled: boolean(data.cloud_enabled),
    cloudUrl: data.cloud_url == null ? '' : string(data.cloud_url),
    hasCloudKey: boolean(data.has_cloud_key),
    tier: string(data.tier),
    timeoutSeconds: number(data.timeout_seconds),
    queueTimeoutSeconds: number(data.queue_timeout_seconds)
  }
}

export const getDocumentParsingSettings = (signal: AbortSignal) =>
  http.request(settingsPath, { signal, decode: parseSettings })

export const getDocumentParsingStatus = (signal: AbortSignal) =>
  http.request(`${settingsPath}/status`, {
    signal,
    decode: (value) => {
      const data = object(value)
      return {
        local: boolean(object(data.local).available),
        cloud: data.cloud == null ? null : boolean(object(data.cloud).available)
      }
    }
  })

export const updateDocumentParsingSettings = (
  settings: DocumentParsingUpdate,
  signal: AbortSignal
) =>
  http.request(settingsPath, {
    method: 'PATCH',
    signal,
    decode: parseSettings,
    body: {
      expected_version: settings.expectedVersion,
      local_url: settings.localUrl || null,
      local_api_key: settings.localApiKey || null,
      cloud_enabled: settings.cloudEnabled,
      cloud_url: settings.cloudUrl || null,
      cloud_api_key: settings.cloudApiKey || null,
      tier: settings.tier,
      timeout_seconds: settings.timeoutSeconds,
      queue_timeout_seconds: settings.queueTimeoutSeconds
    }
  })
