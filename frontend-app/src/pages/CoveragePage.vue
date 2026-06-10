<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listCoverage } from '../api/coverage'
import { asList, fmtNumber } from '../api/client'
import { triggerJob } from '../api/jobs'

const rows = ref<Record<string, any>[]>([])
const summary = ref<Record<string, any>>({})
const error = ref('')

const sortedRows = computed(() => rows.value.slice().sort((a, b) => normalizedPct(b) - normalizedPct(a)))
const totalSku = computed(() => Number(summary.value.total_current_sku ?? rows.value.reduce((sum, row) => sum + Number(row.current || row.sku_count || row.products || row.count || 0), 0)))
const totalEstimated = computed(() => Number(summary.value.total_estimated_full ?? rows.value.reduce((sum, row) => sum + Number(row.estimated_full || 0), 0)))
const coveragePct = computed(() => {
  if (summary.value.overall_coverage_pct != null) return Number(summary.value.overall_coverage_pct)
  if (!totalEstimated.value) return 0
  return Math.round((totalSku.value / totalEstimated.value) * 100)
})
const healthySites = computed(() => Number(summary.value.healthy_count ?? rows.value.filter((row) => normalizedPct(row) >= 90).length))
const warningSites = computed(() => rows.value.filter((row) => {
  if (summary.value.warning_count != null) return false
  const pct = normalizedPct(row)
  return pct >= 50 && pct < 90
}).length)
const warningCount = computed(() => Number(summary.value.warning_count ?? warningSites.value))
const criticalSites = computed(() => Number(summary.value.critical_count ?? rows.value.filter((row) => normalizedPct(row) < 50).length))

async function load() {
  try {
    const data = await listCoverage()
    rows.value = asList(data, ['sites', 'items', 'coverage'])
    summary.value = data?.summary || {}
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function trigger(site: string) {
  await triggerJob({ site })
  await load()
}

function normalizedPct(row: Record<string, any>) {
  const raw = Number(row.coverage_pct || row.coverage || 0)
  return Math.min(100, raw <= 1 ? raw * 100 : raw)
}

function width(row: Record<string, any>) {
  return `${normalizedPct(row)}%`
}

onMounted(load)
</script>

<template>
  <section>
    <div class="lead">数据覆盖率 · 全 {{ rows.length }} 站</div>
    <div class="sub">商品总数 {{ fmtNumber(totalSku) }} / 预计 {{ fmtNumber(totalEstimated) }} = {{ coveragePct }}%</div>
    <UAlert v-if="error" color="error" variant="soft" :title="error" class="mb-4" />

    <div class="stats-hero">
      <div class="stat"><div class="lbl">健康</div><div class="val">{{ healthySites }}</div><div class="delta">≥ 90%</div></div>
      <div class="stat"><div class="lbl">警告</div><div class="val">{{ warningCount }}</div><div class="delta warn">50-90%</div></div>
      <div class="stat"><div class="lbl">关键</div><div class="val">{{ criticalSites }}</div><div class="delta bad">&lt; 50%</div></div>
    </div>

    <div class="cov-grid">
      <div v-for="row in sortedRows" :key="row.site || row.name" class="cov-tile" :class="row.status">
        <h6>{{ row.site || row.name }}</h6>
        <div class="country">{{ row.brand || '—' }} · {{ row.country || '—' }}</div>
        <div class="num">{{ fmtNumber(row.current || row.sku_count || row.products || row.count) }}</div>
        <div class="pct">{{ row.coverage_pct ?? row.coverage ?? '—' }}% · 满 {{ fmtNumber(row.estimated_full || 0) }}</div>
        <div class="bar"><div :style="{ width: width(row) }" /></div>
        <button @click="trigger(row.site || row.name)">▶ 触发</button>
      </div>
    </div>
    <div v-if="!rows.length" class="empty-state">
      <b>当前工作区还没有覆盖率数据</b>
      请先在设置里加入站点，或切换到已有站点的工作区。
    </div>
  </section>
</template>
