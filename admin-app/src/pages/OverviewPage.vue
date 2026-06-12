<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { jobStats } from '../api/queue'
import { health } from '../api/health'
import { usageSummary } from '../api/usage'
import { fmtDate, fmtNumber } from '../api/client'
import StatCard from '../components/common/StatCard.vue'
import StatusBadge from '../components/common/StatusBadge.vue'

const POLL_MS = 5000

const stats = ref<Record<string, number>>({})
const usage = ref<Record<string, any>>({})
const healthInfo = ref<Record<string, any>>({})
const loading = ref(false)
const error = ref('')

const polling = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const queueCards = computed(() => [
  { key: 'pending', label: '队列 · 待处理', value: stats.value.pending ?? 0 },
  { key: 'running', label: '队列 · 运行中', value: stats.value.running ?? 0 },
  { key: 'success', label: '队列 · 成功', value: stats.value.success ?? 0 },
  { key: 'failed', label: '队列 · 失败', value: stats.value.failed ?? 0 },
  { key: 'stuck', label: '队列 · 卡住', value: stats.value.stuck ?? 0 }
])

const usageCards = computed(() => [
  { key: 'credits', label: '总积分消耗', value: fmtNumber(usage.value.total_credits) },
  { key: 'records', label: '总记录数', value: fmtNumber(usage.value.total_records) }
])

const stuckRunning = computed(() => healthInfo.value?.reclaim_hint?.stuck_running ?? 0)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [s, u, h] = await Promise.all([jobStats(), usageSummary(), health()])
    stats.value = s || {}
    usage.value = u || {}
    healthInfo.value = h || {}
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  if (polling.value) timer = setInterval(load, POLL_MS)
}

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function togglePolling() {
  polling.value ? startPolling() : stopPolling()
}

onMounted(() => {
  load()
  startPolling()
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1 class="page-title">概览</h1>
      <label class="poll-toggle">
        <input v-model="polling" type="checkbox" @change="togglePolling" />
        <span>自动刷新 (5s)</span>
      </label>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <section class="block">
      <div class="block-head">
        <h2 class="block-title">Worker 健康</h2>
        <StatusBadge :status="healthInfo.worker_status" />
      </div>
      <div class="block-meta">
        <span>最近活动：{{ fmtDate(healthInfo.last_activity_at) }}</span>
        <span>待处理：{{ fmtNumber(healthInfo.pending) }}</span>
        <span>卡住运行：{{ fmtNumber(stuckRunning) }}</span>
      </div>
    </section>

    <section class="block">
      <h2 class="block-title">用量</h2>
      <div class="stat-row stat-row-2">
        <StatCard v-for="c in usageCards" :key="c.key" :label="c.label" :value="c.value" />
      </div>
    </section>

    <section class="block">
      <h2 class="block-title">队列状态</h2>
      <div class="stat-row">
        <StatCard v-for="c in queueCards" :key="c.key" :label="c.label" :value="c.value" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
}

.poll-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  opacity: 0.8;
  cursor: pointer;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.block-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.block-title {
  font-size: 15px;
  font-weight: 600;
  opacity: 0.85;
}

.block-meta {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  font-size: 13px;
  opacity: 0.7;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}

.stat-row-2 {
  grid-template-columns: repeat(2, minmax(0, 240px));
}

.error {
  font-size: 13px;
  color: #ef4444;
}
</style>
