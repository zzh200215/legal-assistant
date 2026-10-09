<template>
  <ol class="document-version-list">
    <li v-for="item in versions" :key="item.id || item.version" class="document-version">
      <button class="version-select" type="button" @click="$emit('select', item)">
        <span class="version-heading"><strong>Version {{ item.version || 1 }}</strong><time>{{ formatDate(item.created_at) }}</time></span>
        <span v-if="item.status_at_snapshot" class="version-status">{{ statusLabel(item.status_at_snapshot) }}</span>
      </button>
      <p v-if="item.version_note">{{ item.version_note }}</p>
      <div class="version-actions">
        <button type="button" @click.stop="$emit('set-base', item)">设为起点</button>
        <button type="button" @click.stop="$emit('set-target', item)">设为终点</button>
      </div>
    </li>
    <li v-if="!versions.length" class="version-empty">尚无历史版本</li>
  </ol>
</template>

<script setup>
import { statusLabel } from '../../composables/useLegalWorkspacePresentation'

defineProps({ versions: { type: Array, default: () => [] } })
defineEmits(['select', 'set-base', 'set-target'])
const formatDate = (value) => (value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录')
</script>

<style scoped>
.document-version-list { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
.document-version { padding: 11px 0; border-bottom: 1px solid var(--color-border-light); }
.version-select { display: grid; width: 100%; gap: 4px; padding: 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.version-select:hover strong { color: var(--color-primary); }
.version-heading { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
.version-heading strong { font-weight: 600; }
.version-heading time, .version-status, .version-empty { color: var(--color-text-muted); font-size: 11px; }
.document-version p { margin: 5px 0 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.version-actions { display: flex; gap: 10px; margin-top: 7px; }
.version-actions button { padding: 0; border: 0; background: transparent; color: var(--color-primary); font-size: 11px; cursor: pointer; }
.version-actions button:hover { text-decoration: underline; }
</style>
