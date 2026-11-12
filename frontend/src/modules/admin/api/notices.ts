import { http } from '../../../shared/http.ts'
import { list, number, object, string } from '../../../shared/validation.ts'

export interface PublishedNotice {
  id: string
  version: number
  title: string
  content: string
}

export function listPublishedNotices(signal: AbortSignal): Promise<PublishedNotice[]> {
  return http.request('/admin/notices/published', {
    signal,
    decode: (value) =>
      list((item) => {
        const notice = object(item)
        return {
          id: string(notice.id),
          version: notice.version === undefined ? 1 : number(notice.version),
          title: string(notice.title),
          content: string(notice.content)
        }
      })(object(value).items)
  })
}
