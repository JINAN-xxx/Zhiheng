const reportLogoutFailure = () => {
  console.warn('服务端注销失败，本地会话已清理。')
}

const serverLogoutGraceMs = 50

export async function bestEffortLogout(logoutRequest, token, onError = reportLogoutFailure) {
  if (!token) return

  let timeoutId
  try {
    await Promise.race([
      Promise.resolve().then(() => logoutRequest(token)),
      new Promise(resolve => {
        timeoutId = setTimeout(resolve, serverLogoutGraceMs)
      }),
    ])
  } catch (error) {
    onError(error)
  } finally {
    clearTimeout(timeoutId)
  }
}
