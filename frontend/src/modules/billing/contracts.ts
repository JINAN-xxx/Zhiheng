const decimal = /^-?(0|[1-9]\d*)(\.\d{1,18})?$/

export function parsePoints(value: unknown): string {
  if (typeof value !== 'string' || !decimal.test(value))
    throw new Error('服务器返回的积分须为十进制字符串')
  return value
}
