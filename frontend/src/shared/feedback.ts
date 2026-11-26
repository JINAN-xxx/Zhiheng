import { h } from 'vue'
import {
  CheckCircleFilled,
  CloseCircleFilled,
  CloseOutlined,
  ExclamationCircleFilled,
  InfoCircleFilled
} from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'

const icons = {
  success: CheckCircleFilled,
  error: CloseCircleFilled,
  warning: ExclamationCircleFilled,
  info: InfoCircleFilled
}

type Feedback = string | { content: string; duration?: number; key?: string }
type FeedbackType = keyof typeof icons

message.config({
  top: 'max(16px, env(safe-area-inset-top))',
  maxCount: 3
})

function show(type: FeedbackType, input: Feedback) {
  const options = typeof input === 'string' ? { content: input } : input
  const close = message.open({
    ...options,
    type,
    duration: options.duration ?? (type === 'error' ? 6 : 3),
    class: 'platform-feedback',
    icon: h('span', { class: 'platform-feedback__icon', 'aria-hidden': 'true' }, h(icons[type])),
    content: h('span', { class: 'platform-feedback__body' }, [
      h(
        'span',
        { class: 'platform-feedback__text', role: type === 'error' ? 'alert' : 'status' },
        options.content
      ),
      h(
        'button',
        {
          type: 'button',
          class: 'platform-feedback__close',
          'aria-label': '关闭提示',
          onClick: () => close()
        },
        h(CloseOutlined, { 'aria-hidden': 'true' })
      )
    ])
  })
  return close
}

export const feedback = {
  success: (input: Feedback) => show('success', input),
  error: (input: Feedback) => show('error', input),
  warning: (input: Feedback) => show('warning', input),
  info: (input: Feedback) => show('info', input)
}
