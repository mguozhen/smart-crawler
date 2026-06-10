<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { asList } from '../api/client'
import { listCoverage } from '../api/coverage'
import { triggerJob } from '../api/jobs'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const rows = ref<Record<string, any>[]>([])
const loading = ref(false)
const error = ref('')
const sortedRows = computed(() => rows.value.slice().sort((a, b) => normalizedPct(b) - normalizedPct(a)))

function normalizedPct(row: Record<string, any>) {
  const raw = Number(row.coverage_pct || row.coverage || 0)
  return Math.min(100, raw <= 1 ? raw * 100 : raw)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    rows.value = asList(await listCoverage(), ['sites', 'items', 'coverage'])
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function trigger(site: string) {
  await triggerJob({ site })
  await load()
}

function siteKey(row: Record<string, any>) {
  return String(row.site || row.name || '')
}

function reportHref(row: Record<string, any>) {
  const params = new URLSearchParams({ site: siteKey(row) })
  if (auth.workspaceId) params.set('workspace_id', auth.workspaceId)
  return `/report?${params.toString()}`
}

onMounted(load)
</script>

<template>
  <section>
    <div class="lead">站点报表</div>
    <div class="sub">交互式网页报表 · 可自定义模块、列和时间范围 · 点站点打开</div>
    <UAlert v-if="error" color="error" variant="soft" :title="error" class="mb-4" />
    <div class="report-grid">
      <div v-for="s in sortedRows" :key="s.site || s.name" class="report-tile" :class="[s.status, { ready: (s.current || s.sku_count || s.products || s.count || 0) > 0, empty: !(s.current || s.sku_count || s.products || s.count || 0) }]">
        <div class="head">
          <h6>{{ s.site || s.name }}</h6>
          <span v-if="s.status === 'healthy'" class="badge healthy">健康</span>
          <span v-else-if="s.status === 'warning'" class="badge warning">部分</span>
          <span v-else-if="s.status === 'critical'" class="badge critical">异常</span>
          <span v-else class="badge pending">未采集</span>
        </div>
        <div class="country">{{ s.brand || '—' }} · {{ s.country || '—' }}</div>
        <div class="nums">
          <div class="item"><div class="lbl">实际抓取</div><div class="v">{{ (s.current || s.sku_count || s.products || s.count || 0).toLocaleString() }}</div></div>
          <div class="item"><div class="lbl">预计商品</div><div class="v dim">{{ (s.estimated_full || 0).toLocaleString() }}</div></div>
        </div>
        <div class="covbar" :class="s.status"><i :style="{ width: Math.min(Number(s.coverage_pct || s.coverage || 0), 100) + '%' }"></i></div>
        <div class="report-rate">覆盖率 {{ s.coverage_pct != null ? s.coverage_pct + '%' : '—' }}</div>
        <div class="btns">
          <a v-if="(s.current || s.sku_count || s.products || s.count || 0) > 0" :href="reportHref(s)" target="_blank" rel="noopener" class="btn-prim">📊 打开报表</a>
          <button v-else class="btn-muted" :disabled="loading" @click="trigger(siteKey(s))">▶ 触发抓取</button>
        </div>
      </div>
    </div>
    <div v-if="!rows.length" class="empty-state">
      <b>当前工作区还没有站点报表</b>
      请先在设置里给工作区加入站点。
    </div>
  </section>
</template>
