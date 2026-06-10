import { apiJson } from './client'

export function listCoverage() {
  return apiJson('/api/coverage')
}
