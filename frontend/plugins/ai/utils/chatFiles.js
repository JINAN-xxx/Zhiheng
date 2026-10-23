import { normalizeErrorMessage } from '../../../src/utils/errorMessage.js'

const MB = 1024 * 1024
const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif'])
const DOCUMENT_EXTENSIONS = new Set(['pdf', 'docx', 'txt', 'md', 'xlsx', 'csv'])
export const CHAT_FILE_ACCEPT = '.png,.jpg,.jpeg,.webp,.gif,.pdf,.docx,.txt,.md,.xlsx,.csv'

export function chatImageHint({ visionEnabled = true, model } = {}) {
  if (!visionEnabled) return '请在对话偏好中启用视觉后添加图片；文档附件仍可上传。'
  if (!model) return '请先选择支持视觉的模型后添加图片；文档附件仍可上传。'
  if (model.capabilities?.vision === false) return '当前模型未启用视觉能力，请切换支持图片的模型；文档附件仍可上传。'
  if (model.capabilities?.vision !== true) return '当前模型尚未配置视觉能力，请确认模型配置后添加图片；文档附件仍可上传。'
  return ''
}

export function chatFileLabel(file = {}, kind = 'attachment') {
  return file.fileName || file.originalFilename || (kind === 'image' ? '图片' : '附件')
}

export function chatFileDownloadPath(file) {
  return `/common/files/${encodeURIComponent(file.fileId)}/download/${encodeURIComponent(file.fileName || file.originalFilename || 'attachment')}`
}

export async function chatImagePreviewBlob(blob) {
  // Protected downloads intentionally use octet-stream. Detect only supported raster signatures.
  const bytes = new Uint8Array(await blob.slice(0, 12).arrayBuffer())
  const startsWith = signature => signature.every((value, index) => bytes[index] === value)
  const text = String.fromCharCode(...bytes)
  let mimeType = ''
  if (startsWith([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) mimeType = 'image/png'
  else if (startsWith([0xff, 0xd8, 0xff])) mimeType = 'image/jpeg'
  else if (text.startsWith('GIF87a') || text.startsWith('GIF89a')) mimeType = 'image/gif'
  else if (text.startsWith('RIFF') && text.slice(8, 12) === 'WEBP') mimeType = 'image/webp'
  if (!mimeType) throw new Error('该文件无法作为图片预览')
  return new Blob([blob], { type: mimeType })
}

export function assertChatUploadResponse(response) {
  if (response?.code !== 200 || typeof response.fileId !== 'string' || !response.fileId.trim()) {
    throw new Error(normalizeErrorMessage(response, '文件上传失败，服务未返回有效的文件信息'))
  }
  return response
}

export function chatFilePayload(items = []) {
  if (items.some(item => item.status === 'uploading')) throw new Error('请等待文件上传完成')
  if (items.some(item => item.status !== 'ready' || !item.fileId)) throw new Error('请重试或移除上传失败的文件后再发送')
  return {
    images: items.filter(item => item.kind === 'image').map(({ fileId }) => ({ fileId })),
    attachments: items.filter(item => item.kind === 'attachment').map(({ fileId }) => ({ fileId })),
  }
}

export function createChatFileQueue({ upload, onChange = () => {}, createObjectURL = file => URL.createObjectURL(file), revokeObjectURL = url => URL.revokeObjectURL(url) }) {
  let entries = []
  let sequence = 0
  const snapshots = () => entries.map(({ file, controller, ...item }) => ({ ...item }))
  const publish = () => onChange(snapshots())
  const isCurrent = (entry, controller) => entries.includes(entry) && entry.controller === controller && !controller.signal.aborted

  async function startUpload(entry) {
    const controller = new AbortController()
    entry.controller = controller
    entry.status = 'uploading'
    entry.progress = 0
    entry.error = ''
    publish()
    try {
      const response = await upload(entry.file, {
        signal: controller.signal,
        onUploadProgress(event) {
          if (!isCurrent(entry, controller)) return
          entry.progress = event.total ? Math.min(99, Math.round(event.loaded / event.total * 100)) : 0
          publish()
        },
      })
      if (!isCurrent(entry, controller)) return
      const metadata = assertChatUploadResponse(response)
      entry.fileId = metadata.fileId
      entry.fileName = metadata.originalFilename || entry.fileName
      entry.status = 'ready'
      entry.progress = 100
    } catch (error) {
      if (!isCurrent(entry, controller)) return
      entry.status = 'failed'
      entry.error = normalizeErrorMessage(error, '文件上传失败，请重试')
    } finally {
      if (isCurrent(entry, controller)) publish()
    }
  }

  function release(entry) {
    entry.controller?.abort()
    if (entry.previewUrl) revokeObjectURL(entry.previewUrl)
  }

  return {
    get items() { return snapshots() },
    add(files, settings = {}) {
      const accepted = [], rejected = []
      for (const file of Array.from(files || [])) {
        const extension = String(file.name || '').split('.').pop().toLowerCase()
        const kind = IMAGE_EXTENSIONS.has(extension) ? 'image' : DOCUMENT_EXTENSIONS.has(extension) ? 'attachment' : null
        const imageLimit = Number(settings.imageMaxSizeMb)
        const maxSizeMb = kind === 'image' ? Math.min(50, Math.max(1, Number.isFinite(imageLimit) && imageLimit > 0 ? imageLimit : 10)) : 20
        const maxCount = kind === 'image' ? 10 : 5
        let error = ''
        if (!kind) error = '不支持此文件类型，请选择图片或 PDF、DOCX、TXT、MD、XLSX、CSV 文档'
        else if (kind === 'image' && chatImageHint(settings)) error = chatImageHint(settings)
        else if (!file.size) error = '不能上传空文件'
        else if (file.size > maxSizeMb * MB) error = `单个${kind === 'image' ? '图片' : '文档'}不能超过 ${maxSizeMb} MB`
        else if (entries.filter(item => item.kind === kind).length >= maxCount) error = `每条消息最多添加 ${maxCount} ${kind === 'image' ? '张图片' : '个文档'}`
        if (error) {
          rejected.push({ fileName: file.name, error })
          continue
        }
        const entry = {
          id: `chat-file-${++sequence}`, file, kind, fileName: file.name,
          mimeType: file.type || '', sizeBytes: file.size, fileId: null,
          status: 'uploading', progress: 0, error: '',
          previewUrl: kind === 'image' ? createObjectURL(file) : '',
        }
        // Reserve synchronously so another chooser batch cannot overflow pending slots.
        entries.push(entry)
        accepted.push(entry)
      }
      publish()
      return { rejected, uploads: Promise.all(accepted.map(startUpload)) }
    },
    retry(id) {
      const entry = entries.find(item => item.id === id)
      return entry?.status === 'failed' ? startUpload(entry) : Promise.resolve()
    },
    remove(id) {
      const entry = entries.find(item => item.id === id)
      if (!entry) return
      entries = entries.filter(item => item !== entry)
      release(entry)
      publish()
    },
    clear() {
      const previous = entries
      entries = []
      previous.forEach(release)
      publish()
    },
  }
}
