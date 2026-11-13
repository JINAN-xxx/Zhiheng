import { message } from 'ant-design-vue'
import { copyText } from './clipboard.ts'

// Markdown's copy event is emitted after its built-in async-clipboard attempt.
// On LAN HTTP that API is absent, so complete the copy through our fallback.
export function copyMarkdownCode(value: unknown): void {
  if (typeof value !== 'string' || typeof navigator.clipboard?.writeText === 'function') return
  void copyText(value).catch(() => {
    void message.error('复制失败，请手动选择代码后复制')
  })
}
