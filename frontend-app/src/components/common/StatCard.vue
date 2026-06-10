<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  note?: string
}>()
</script>

<template>
  <div class="sc-card p-5">
    <p class="sc-muted text-sm">{{ label }}</p>
    <p class="mt-2 text-3xl font-semibold text-white">{{ value }}</p>
    <p v-if="note" class="sc-muted mt-2 text-xs">{{ note }}</p>
  </div>
</template>
