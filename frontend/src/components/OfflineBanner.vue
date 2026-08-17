<template>
  <div v-if="!online" class="offline-banner" role="status">
    <span class="offline-dot"></span>
    <span>网络不可用，已暂停刷新与同步。恢复网络后自动继续。</span>
  </div>
</template>

<script setup>
import { useNetworkState } from '../composables/useNetworkState'

const { online } = useNetworkState()
</script>

<style scoped>
.offline-banner {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--color-warning-light);
  background: var(--color-warning-light);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  position: sticky;
  top: 0;
  z-index: 40;
}
.offline-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-warning);
  flex: 0 0 auto;
}
</style>
