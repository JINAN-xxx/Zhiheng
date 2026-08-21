import { h, ref, render } from 'vue'
import { Input, Modal, Spin, message, notification } from 'ant-design-vue'

let feedbackApis
let loadingContainer

export function registerFeedbackApis(apis) {
  feedbackApis = apis
  return () => {
    if (feedbackApis === apis) {
      feedbackApis = undefined
    }
  }
}

function getMessageApi() {
  return feedbackApis?.message ?? message
}

function getNotificationApi() {
  return feedbackApis?.notification ?? notification
}

function getModalApi() {
  return feedbackApis?.modal ?? Modal
}

function showMessage(type, content, duration) {
  return getMessageApi()[type]({ content, duration })
}

function showAlert(type, content) {
  return new Promise((resolve) => {
    getModalApi()[type]({
      title: '系统提示',
      content,
      okText: '确定',
      centered: true,
      onOk: resolve,
    })
  })
}

function showNotification(type, content) {
  return getNotificationApi()[type]({
    message: '系统通知',
    description: content,
    placement: 'topRight',
  })
}

const feedback = {
  msg(content, duration) {
    return showMessage('info', content, duration)
  },
  msgError(content, duration) {
    return showMessage('error', content, duration)
  },
  msgSuccess(content, duration) {
    return showMessage('success', content, duration)
  },
  msgWarning(content, duration) {
    return showMessage('warning', content, duration)
  },
  alert(content) {
    return showAlert('info', content)
  },
  alertError(content) {
    return showAlert('error', content)
  },
  alertSuccess(content) {
    return showAlert('success', content)
  },
  alertWarning(content) {
    return showAlert('warning', content)
  },
  notify(content) {
    return showNotification('info', content)
  },
  notifyError(content) {
    return showNotification('error', content)
  },
  notifySuccess(content) {
    return showNotification('success', content)
  },
  notifyWarning(content) {
    return showNotification('warning', content)
  },
  confirm(content) {
    return new Promise((resolve, reject) => {
      getModalApi().confirm({
        title: '系统提示',
        content,
        okText: '确定',
        cancelText: '取消',
        centered: true,
        onOk: () => resolve(true),
        onCancel: () => reject('cancel'),
      })
    })
  },
  prompt(content) {
    const value = ref('')
    return new Promise((resolve, reject) => {
      getModalApi().confirm({
        title: '系统提示',
        content: () =>
          h('div', { class: 'antd-feedback-prompt' }, [
            h('p', { class: 'antd-feedback-prompt__label' }, content),
            h(Input, {
              autofocus: true,
              value: value.value,
              'onUpdate:value': (nextValue) => {
                value.value = nextValue
              },
            }),
          ]),
        okText: '确定',
        cancelText: '取消',
        centered: true,
        onOk: () => resolve({ value: value.value }),
        onCancel: () => reject('cancel'),
      })
    })
  },
  loading(content = '正在处理，请稍候') {
    this.closeLoading()
    if (typeof document === 'undefined') return

    loadingContainer = document.createElement('div')
    loadingContainer.className = 'antd-global-loading'
    document.body.appendChild(loadingContainer)
    render(
      h('div', { class: 'antd-global-loading__surface' }, [
        h(Spin, { size: 'large' }),
        h('span', { class: 'antd-global-loading__text' }, content),
      ]),
      loadingContainer,
    )
  },
  closeLoading() {
    if (!loadingContainer) return
    render(null, loadingContainer)
    loadingContainer.remove()
    loadingContainer = undefined
  },
}

export default feedback
