function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
export function object(value: unknown): Record<string, unknown> {
  if (!isObject(value)) {
    throw new Error('服务器返回的数据格式不正确')
  }
  return value
}
export function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('服务器返回的文本格式不正确')
  return value
}
export function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw new Error('服务器返回的数值格式不正确')
  return value
}
export function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('服务器返回的布尔值格式不正确')
  return value
}
export function list<T>(decode: (value: unknown) => T): (value: unknown) => T[] {
  return (value) => {
    if (!Array.isArray(value)) throw new Error('服务器返回的列表格式不正确')
    return value.map(decode)
  }
}
export function nullableNumber(value: unknown): number | null {
  return value === null ? null : number(value)
}
