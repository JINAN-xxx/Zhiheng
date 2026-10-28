import { ApiError, errorMessage } from '../../../shared/http.ts'
export function adminError(error: unknown): string {
  return error instanceof ApiError && error.status === 409
    ? `${errorMessage(error)}。数据可能已被修改，请刷新列表后重新打开编辑；当前输入已保留。`
    : errorMessage(error)
}
