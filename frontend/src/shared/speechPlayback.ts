export type SpeechState = 'idle' | 'starting' | 'playing'
export interface SpeechCallbacks {
  start: () => void
  end: () => void
  error: (reason: string) => void
}
export interface SpeechPlaybackPort {
  available: () => boolean
  start: (text: string, events: SpeechCallbacks) => void
  cancel: () => void
}
export function createSpeechPlayback(port: SpeechPlaybackPort) {
  let active: {
    owner: symbol | string
    state: (value: SpeechState) => void
    token: number
  } | null = null
  let sequence = 0
  let startTimeout: ReturnType<typeof setTimeout> | undefined
  function stop(owner?: symbol | string): void {
    if (!active || (owner !== undefined && active.owner !== owner)) return
    const previous = active
    active = null
    clearTimeout(startTimeout)
    previous.state('idle')
    port.cancel()
  }
  function toggle(
    owner: symbol | string,
    text: string,
    state: (value: SpeechState) => void,
    error: (reason: string) => void
  ): void {
    if (active?.owner === owner) {
      stop(owner)
      return
    }
    stop()
    if (!port.available()) {
      error('当前浏览器不支持朗读')
      return
    }
    if (!text.trim()) {
      error('当前消息没有可朗读的文本')
      return
    }
    const token = ++sequence
    active = { owner, state, token }
    state('starting')
    const current = () => active?.token === token
    const fail = (reason: string) => {
      if (!current()) return
      stop(owner)
      error(`朗读失败：${reason}`)
    }
    startTimeout = setTimeout(() => fail('浏览器未开始播放，请检查系统语音设置后重试'), 10000)
    try {
      port.start(text, {
        start: () => {
          if (current()) {
            clearTimeout(startTimeout)
            state('playing')
          }
        },
        end: () => {
          if (current()) stop(owner)
        },
        error: fail
      })
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : '语音服务不可用')
    }
  }
  return { toggle, stop }
}

export const speechPlayback = createSpeechPlayback({
  available: () =>
    typeof window !== 'undefined' &&
    'speechSynthesis' in window &&
    typeof SpeechSynthesisUtterance !== 'undefined',
  start: (text, events) => {
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = document.documentElement.lang || 'zh-CN'
    utterance.onstart = events.start
    utterance.onend = events.end
    utterance.onerror = (event) => events.error(event.error)
    window.speechSynthesis.speak(utterance)
  },
  cancel: () => {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window)
      window.speechSynthesis.cancel()
  }
})
if (typeof window !== 'undefined') window.addEventListener('pagehide', () => speechPlayback.stop())
