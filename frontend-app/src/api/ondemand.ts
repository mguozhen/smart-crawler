import { apiJson, jsonBody, qs } from './client'

export function fetchOndemand(payload: Record<string, unknown>) {
  return apiJson('/api/ondemand/fetch', { method: 'POST', ...jsonBody(payload) })
}

export function batchOndemand(payload: Record<string, unknown>) {
  return apiJson('/api/ondemand/batch', { method: 'POST', ...jsonBody(payload) })
}

export function listOndemandJobs(params: Record<string, unknown> = {}) {
  return apiJson(`/api/ondemand/jobs${qs(params)}`)
}

export function getOndemandJob(id: string | number) {
  return apiJson(`/api/ondemand/jobs/${id}`)
}

export function retryOndemandJob(id: string | number) {
  return apiJson(`/api/ondemand/jobs/${id}/retry`, { method: 'POST' })
}

export function deleteOndemandJob(id: string | number) {
  return apiJson(`/api/ondemand/jobs/${id}`, { method: 'DELETE' })
}

export function clearOndemandJobs() {
  return apiJson('/api/ondemand/jobs', { method: 'DELETE' })
}
