export const singleSelection = (value: unknown): string => (typeof value === 'string' ? value : '')
export const multipleSelection = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
export const numberInput = (value: unknown): number =>
  typeof value === 'number' ? value : Number.NaN
