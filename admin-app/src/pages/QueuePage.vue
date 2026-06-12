<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { enqueueJob, jobStats, listJobs, retryJob } from '../api/queue'
import { fmtDate } from '../api/client'
import StatCard from '../components/common/StatCard.vue'
import StatusBadge from '../components/common/StatusBadge.vue'

const POLL_MS = 5000

const stats = ref<Record<string, number>>({})
const items = ref<any[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

const statusFilter = ref('')
const page = ref(1)
const size = ref(20)

const polling = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const enqForm = ref({ url: '', dataset: '' })
const enqBusy = ref(false)
const enqMsg = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))

const statCards = computed(() => [
  { key: 'pending', label: '待处理', value: stats.value.pending ?? 0 },
  { key: 'running', label: '运行中', value: stats.value.running ?? 0 },
  { key: 'success', label: '成功', value: stats.value.success ?? 0 },
  { key: 'failed', label: '失败', value: stats.value.failed ?? 0 },
  { key: 'stuck', label: '卡住', value: stats.value.stuck ?? 0 }
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [s, jobs] = await Promise.all([
      jobStats(),
      listJobs({ status: statusFilter.value, page: page.value, size: size.value })
    ])
    stats.value = s || {}
    items.value = jobs?.items ?? []
    total.value = jobs?.total ?? 0
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

watch(polling, (on) => (on ? startPolling() : stopPolling()))

watch([statusFilter, size], () => {
  page.value = 1
  load()
})

async function submitEnqueue() {
  if (!enqForm.value.url || !enqForm.value.dataset) {
    enqMsg.value = '请填写 URL 与 dataset'
    return
  }
  enqBusy.value = true
  enqMsg.value = ''
  try {
    const res = await enqueueJob({ url: enqForm.value.url, dataset: enqForm.value.dataset })
    enqMsg.value = `已入队 #${res?.job_id ?? '-'}`
    enqForm.value.url = ''
    await load()
  } catch (err) {
    enqMsg.value = err instanceof Error ? err.message : String(err)
  } finally {
    enqBusy.value = false
  }
}

async function doRetry(id: number) {
  try {
    await retryJob(id)
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function changePage(delta: number) {
  const next = page.value + delta
  if (next < 1 || next > totalPages.value) return
  page.value = next
  load()
}

function truncate(s?: string | null, n = 48) {
  const v = String(s || '')
  return v.length > n ? `${v.slice(0, n)}…` : v
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
      <h1 class="page-title">队列</h1>
      <label class="poll-toggle">
        <input v-model="polling" type="checkbox" />
        <span>自动刷新 (5s)</span>
      </label>
    </div>

    <div class="stat-row">
      <StatCard v-for="c in statCards" :key="c.key" :label="c.label" :value="c.value" />
    </div>

    <div class="toolbar">
      <select v-model="statusFilter" class="ctl">
        <option value="">全部状态</option>
        <option value="pending">待处理</option>
        <option value="running">运行中</option>
        <option value="success">成功</option>
        <option value="failed">失败</option>
      </select>
      <button class="ctl btn" :disabled="loading" @click="load">刷新</button>
    </div>

    <div class="enqueue">
      <input v-model="enqForm.url" class="ctl grow" placeholder="URL" />
      <input v-model="enqForm.dataset" class="ctl" placeholder="dataset" />
      <button class="ctl btn primary" :disabled="enqBusy" @click="submitEnqueue">
        {{ enqBusy ? '入队中…' : '入队' }}
      </button>
      <span v-if="enqMsg" class="enqueue-msg">{{ enqMsg }}</span>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <div class="table-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th>ID</th>
            <th>状态</th>
            <th>dataset</th>
            <th>URL</th>
            <th>重试</th>
            <th>错误</th>
            <th>创建时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in items" :key="row.id">
            <td>{{ row.id }}</td>
            <td><StatusBadge :status="row.status" /></td>
            <td>{{ row.dataset || '-' }}</td>
            <td :title="row.url">{{ truncate(row.url) }}</td>
            <td>{{ row.retries ?? 0 }}</td>
            <td class="err-cell" :title="row.error || ''">{{ truncate(row.error, 36) || '-' }}</td>
            <td>{{ fmtDate(row.created_at) }}</td>
            <td>
              <button class="btn small" @click="doRetry(row.id)">重试</button>
            </td>
          </tr>
          <tr v-if="!items.length">
            <td colspan="8" class="empty">{{ loading ? '加载中…' : '暂无任务' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pager">
      <button class="btn small" :disabled="page <= 1" @click="changePage(-1)">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span>
      <button class="btn small" :disabled="page >= totalPages" @click="changePage(1)">下一页</button>
      <select v-model.number="size" class="ctl">
        <option :value="20">20 / 页</option>
        <option :value="50">50 / 页</option>
        <option :value="100">100 / 页</option>
      </select>
    </div>
  </div>
</template>

<style scoped>
.page {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
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

.stat-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}

.toolbar,
.enqueue {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.ctl {
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--ui-border, rgba(255, 255, 255, 0.12));
  background: var(--ui-bg, rgba(0, 0, 0, 0.15));
  color: inherit;
  font-size: 14px;
}

.grow {
  flex: 1;
  min-width: 200px;
}

.btn {
  cursor: pointer;
}

.btn.primary {
  border: none;
  color: #fff;
  background: var(--ui-color-primary-500, #6366f1);
}

.btn.small {
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  border: 1px solid var(--ui-border, rgba(255, 255, 255, 0.12));
  background: transparent;
  color: inherit;
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.enqueue-msg {
  font-size: 13px;
  opacity: 0.75;
}

.error {
  font-size: 13px;
  color: #ef4444;
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--ui-border, rgba(255, 255, 255, 0.08));
  border-radius: 12px;
}

.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.tbl th,
.tbl td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid var(--ui-border, rgba(255, 255, 255, 0.06));
  white-space: nowrap;
}

.tbl th {
  font-weight: 600;
  opacity: 0.7;
}

.err-cell {
  max-width: 240px;
  color: #ef4444;
}

.empty {
  text-align: center;
  opacity: 0.6;
  padding: 24px;
}

.pager {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}
</style>
