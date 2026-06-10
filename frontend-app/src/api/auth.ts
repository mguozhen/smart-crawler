import { apiJson, jsonBody } from './client'

export function login(payload: { username: string; password: string }) {
  return apiJson('/api/auth/login', { method: 'POST', ...jsonBody(payload) })
}

export function register(payload: { invite_code: string; username: string; password: string; email?: string; display_name?: string; confirm_password?: string }) {
  return apiJson('/api/auth/register', { method: 'POST', ...jsonBody(payload) })
}

export function logout() {
  return apiJson('/api/auth/logout', { method: 'POST' })
}

export function me() {
  return apiJson('/api/me')
}

export function updateMe(payload: Record<string, unknown>) {
  return apiJson('/api/me', { method: 'PATCH', ...jsonBody(payload) })
}

export function changePassword(payload: { old_password: string; new_password: string }) {
  return apiJson('/api/auth/change-password', { method: 'POST', ...jsonBody(payload) })
}
