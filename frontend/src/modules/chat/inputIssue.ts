import { ApiError } from '../../shared/http.ts'

export interface ChatInputIssue {
  title: string
  description: string
  statistics: string
}

export function inputIssueFrom(error: unknown): ChatInputIssue | null {
  if (!(error instanceof ApiError) || error.status < 400 || error.status >= 500) return null
  if (
    ![
      'context_input_too_long',
      'context_too_long',
      'attachment_text_too_long',
      'attachment_images_too_large'
    ].includes(error.code)
  )
    return null
  const details =
    typeof error.details === 'object' && error.details !== null
      ? (error.details as Record<string, unknown>)
      : {}
  const estimated = details.estimated_input_tokens
  const budget = details.input_budget
  const statistics =
    typeof estimated === 'number' &&
    Number.isSafeInteger(estimated) &&
    estimated >= 0 &&
    typeof budget === 'number' &&
    Number.isSafeInteger(budget) &&
    budget >= 0
      ? `本次输入约 ${estimated.toLocaleString('zh-CN')} Token · 已设置上限 ${budget.toLocaleString('zh-CN')} Token`
      : ''
  return {
    title:
      error.code === 'attachment_images_too_large'
        ? '本次图片内容过多'
        : '本次内容超过已设置的模型容量',
    description:
      error.code === 'attachment_images_too_large'
        ? '附件和草稿已保留。请压缩图片或移除部分图片后重新发送。'
        : '附件和草稿已保留。请精简附件或提示词后重新发送。',
    statistics
  }
}
