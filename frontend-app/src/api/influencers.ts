import { apiJson, qs } from './client'

export function influencerFull(params: Record<string, unknown>) {
  return apiJson(`/api/influencers/full${qs(params)}`)
}
