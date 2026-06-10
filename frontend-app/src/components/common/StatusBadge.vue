<script setup lang="ts">
defineProps<{ status?: string | null }>()

function tone(status?: string | null) {
  const s = String(status || '').toLowerCase()
  if (['ok', 'success', 'completed', 'active', 'done', 'on_sale', 'healthy'].includes(s)) return 'ok'
  if (['running', 'queued', 'pending', 'warning'].includes(s)) return 'warn'
  if (['failed', 'error', 'disabled', 'out_of_stock', 'critical'].includes(s)) return 'bad'
  return 'pending'
}

function label(status?: string | null) {
  const s = String(status || '')
  return ({ success: '成功', completed: '完成', running: '跑中', failed: '失败', queued: '排队', on_sale: '在售', out_of_stock: '缺货' } as Record<string, string>)[s] || s || '—'
}
</script>

<template>
  <span class="pill" :class="tone(status)">{{ label(status) }}</span>
</template>
