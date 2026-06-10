<script setup lang="ts">
defineProps<{
  buckets: Array<{ label: string; count: number; width: string }>
}>()
</script>

<template>
  <div class="grid gap-4">
    <div v-for="bucket in buckets" :key="bucket.label" class="grid grid-cols-[4rem_1fr_3rem] items-center gap-3 text-sm">
      <span class="sc-muted">{{ bucket.label }}</span>
      <div class="h-3 rounded bg-slate-800">
        <div class="h-3 rounded bg-teal-300" :style="{ width: bucket.width }" />
      </div>
      <span class="text-right">{{ bucket.count }}</span>
    </div>
  </div>
</template>
