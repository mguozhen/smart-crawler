import { apiJson, qs } from './client'

export function listSites() {
  return apiJson('/api/sites')
}

export function listProducts(params: Record<string, unknown> = {}) {
  return apiJson(`/api/products${qs(params)}`)
}

export function getProduct(id: number | string) {
  return apiJson(`/api/products/${id}`)
}

export function productPriceHistory(id: number | string) {
  return apiJson(`/api/products/${id}/price-history`)
}

export function siteOverview(site: string) {
  return apiJson(`/api/sites/${encodeURIComponent(site)}/overview`)
}

export function listPromotions(params: Record<string, unknown> = {}) {
  return apiJson(`/api/promotions${qs(params)}`)
}
