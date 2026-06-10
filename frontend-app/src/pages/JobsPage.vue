<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { asList, fmtDate } from '../api/client'
import { listJobs } from '../api/jobs'
import StatusBadge from '../components/common/StatusBadge.vue'

const jobs = ref<Record<string, any>[]>([])
const error = ref('')

async function load() {
  try {
    jobs.value = asList(await listJobs({ limit: 80 }), ['jobs', 'items'])
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(load)
</script>

<template>
  <section>
    <div class="lead">采集任务 · 进程状态</div>
    <div class="sub">{{ jobs.filter((j) => j.status === 'running').length }} 跑中 · {{ jobs.filter((j) => ['success', 'completed'].includes(j.status)).length }} 成功 · 共 {{ jobs.length }} 显示</div>
    <UAlert v-if="error" color="error" variant="soft" :title="error" class="mb-4" />
    <button class="btn-go" style="margin-bottom:10px" @click="load">🔄 刷新</button>
    <div class="jobs-list">
      <div class="job-row head"><div>#</div><div>站点</div><div>状态</div><div>商品</div><div>耗时</div><div>完成</div></div>
      <div v-for="job in jobs.slice(0, 40)" :key="job.id" class="job-row">
        <div>{{ job.id }}</div>
        <div>{{ job.site || job.brand }}</div>
        <div><StatusBadge :status="job.status" /></div>
        <div>{{ job.products_count || job.product_count || 0 }}</div>
        <div>{{ job.duration_sec ? Math.round(job.duration_sec) + ' 秒' : '—' }}</div>
        <div>{{ (job.finished_at || job.started_at || job.created_at || '').slice(11, 16) || '—' }}</div>
      </div>
      <div v-if="!jobs.length" class="empty-state">
        <b>暂无采集任务</b>
        可以从覆盖率页面触发一个站点抓取。
      </div>
    </div>
  </section>
</template>
