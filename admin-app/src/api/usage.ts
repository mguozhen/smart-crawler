import { apiJson, qs, type Dict } from './client'

export const usageSummary = (p: Dict<any> = {}) => apiJson(`/api/admin/spine/usage${qs(p)}`)
