import type { ProfileUpdate } from './api/profile.ts'

export interface ProfileDraft {
  display_name: string
  email: string
  phone: string
}
export interface PasswordDraft {
  current_password: string
  new_password: string
  confirmation: string
}
export type ProfileErrors = Partial<Record<keyof ProfileDraft, string>>
export type PasswordErrors = Partial<Record<keyof PasswordDraft, string>>

export function profileUpdate(draft: ProfileDraft, version: number): ProfileUpdate {
  return {
    display_name: draft.display_name.trim(),
    email: draft.email.trim(),
    phone: draft.phone.trim(),
    expected_version: version
  }
}

export function validateProfileDraft(draft: ProfileDraft): ProfileErrors {
  const values = profileUpdate(draft, 1)
  const errors: ProfileErrors = {}
  if ([...values.display_name].length > 100) errors.display_name = '显示名称不能超过 100 个字符'
  if ([...values.email].length > 254) errors.email = '邮箱不能超过 254 个字符'
  else if (values.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email))
    errors.email = '请输入有效的邮箱地址'
  if ([...values.phone].length > 40) errors.phone = '联系电话不能超过 40 个字符'
  return errors
}

export function validatePasswordDraft(draft: PasswordDraft): PasswordErrors {
  const errors: PasswordErrors = {}
  if (!draft.current_password) errors.current_password = '请输入当前密码'
  const length = [...draft.new_password].length
  if (length < 12 || length > 1024) errors.new_password = '新密码需要 12–1024 个字符'
  if (!draft.confirmation) errors.confirmation = '请再次输入新密码'
  else if (draft.confirmation !== draft.new_password) errors.confirmation = '两次输入的新密码不一致'
  return errors
}
