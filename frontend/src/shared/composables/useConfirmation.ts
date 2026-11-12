import { onBeforeUnmount } from 'vue'
import { Modal } from 'ant-design-vue'
export function useConfirmation() {
  let close: (() => void) | undefined
  const cancel = (): void => {
    close?.()
  }
  function confirm(title: string): Promise<boolean> {
    cancel()
    return new Promise((resolve) => {
      const dialog = Modal.confirm({
        title,
        okText: '确定',
        cancelText: '取消',
        onOk: () => {
          close = undefined
          resolve(true)
        },
        onCancel: () => {
          close = undefined
          resolve(false)
        }
      })
      close = () => {
        close = undefined
        dialog.destroy()
        resolve(false)
      }
    })
  }
  onBeforeUnmount(cancel)
  return { confirm, cancel }
}
