import { apiJson } from './client'

export const health = () => apiJson('/api/admin/spine/health')
