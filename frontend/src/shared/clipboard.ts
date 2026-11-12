function copyWithSelection(text: string): void {
  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const selection = document.getSelection()
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) =>
        selection.getRangeAt(index).cloneRange()
      )
    : []
  const input =
    active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement ? active : null
  const start = input?.selectionStart
  const end = input?.selectionEnd
  const direction = input?.selectionDirection ?? 'none'
  const field = document.createElement('textarea')
  field.value = text
  field.readOnly = true
  field.tabIndex = -1
  Object.assign(field.style, {
    position: 'fixed',
    top: '0',
    left: '0',
    width: '1px',
    height: '1px',
    padding: '0',
    opacity: '0'
  })
  // Keep focus inside the active dialog while copying a share link.
  const container = active?.closest('[role="dialog"]') ?? document.body
  container.appendChild(field)
  try {
    field.focus({ preventScroll: true })
    field.select()
    field.setSelectionRange(0, text.length)
    if (!document.execCommand('copy')) throw new Error('copy_rejected')
  } finally {
    field.remove()
    if (active?.isConnected) active.focus({ preventScroll: true })
    if (input?.isConnected && typeof start === 'number' && typeof end === 'number') {
      input.setSelectionRange(start, end, direction)
    } else if (selection) {
      selection.removeAllRanges()
      ranges.forEach((range) => selection.addRange(range))
    }
  }
}

export async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return
    }
  } catch {
    // Browsers can expose the API while rejecting clipboard permission.
  }
  try {
    copyWithSelection(text)
  } catch {
    throw new Error('复制失败，请手动选择内容后复制')
  }
}
