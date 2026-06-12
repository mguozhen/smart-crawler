import { apiJson, jsonBody, qs, type Dict } from './client'

export const listJobs = (p: Dict<any> = {}) => apiJson(`/api/admin/spine/jobs${qs(p)}`)
export const jobStats = () => apiJson('/api/admin/spine/jobs/stats')
export const jobDetail = (id: number) => apiJson(`/api/admin/spine/jobs/${id}`)
export const retryJob = (id: number) => apiJson(`/api/admin/spine/jobs/${id}/retry`, { method: 'POST' })
export const enqueueJob = (payload: Dict<any>) =>
  apiJson('/api/admin/spine/jobs/enqueue', { method: 'POST', ...jsonBody(payload) })
