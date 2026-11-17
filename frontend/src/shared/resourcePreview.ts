/** Small resource projections; business filtering and links belong to each module. */
export interface ResourcePreviewQuery {
  keyword: string
  limit: number
}
export interface ResourcePreview {
  id: string
  name: string
  description: string
  path: string
}
export interface ResourcePreviewPage {
  total: number
  entries: ResourcePreview[]
}
