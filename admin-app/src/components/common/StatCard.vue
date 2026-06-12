<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  note?: string
}>()
</script>

<template>
  <div class="stat-card">
    <p class="stat-label">{{ label }}</p>
    <p class="stat-value">{{ value }}</p>
    <p v-if="note" class="stat-note">{{ note }}</p>
  </div>
</template>

<style scoped>
.stat-card {
  padding: 18px 20px;
  border-radius: 12px;
  border: 1px solid var(--ui-border, rgba(255, 255, 255, 0.08));
  background: var(--ui-bg-elevated, rgba(255, 255, 255, 0.03));
  min-width: 0;
}

.stat-label {
  font-size: 13px;
  opacity: 0.65;
}

.stat-value {
  margin-top: 8px;
  font-size: 28px;
  font-weight: 600;
  line-height: 1.1;
}

.stat-note {
  margin-top: 6px;
  font-size: 12px;
  opacity: 0.55;
}
</style>
