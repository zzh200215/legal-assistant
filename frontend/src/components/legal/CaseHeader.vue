<template>
  <header class="case-header">
    <div class="case-heading-row">
      <div class="case-heading-copy">
        <p class="case-kicker">案件 #{{ matter.id }} · {{ caseTypeLabel }}</p>
        <h1>{{ matter.title }}</h1>
      </div>
      <div class="case-heading-actions">
        <span class="case-status" :class="`status-${matter.status || 'in_progress'}`">{{ statusLabel }}</span>
        <button class="primary-action" type="button" @click="$emit('continue')">继续处理</button>
      </div>
    </div>
    <p v-if="matter.description" class="case-description">{{ matter.description }}</p>
    <dl class="case-meta">
      <div><dt>负责人</dt><dd>{{ matter.owner_name || '未指定' }}</dd></div>
      <div><dt>客户</dt><dd>{{ matter.client_name || '未登记' }}</dd></div>
      <div><dt>对方当事人</dt><dd>{{ matter.opposing_party || '未登记' }}</dd></div>
      <div><dt>最近更新</dt><dd>{{ formatDate(matter.updated_at) || '—' }}</dd></div>
    </dl>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { categoryLabel } from '../../composables/useLegalWorkspacePresentation'

const props = defineProps({ matter: { type: Object, required: true } })
defineEmits(['continue'])

const caseTypeLabel = computed(() => categoryLabel(props.matter.case_type) || '其他案件')
const statusLabel = computed(() => ({ in_progress: '进行中', closed: '已结案', archived: '已归档' }[props.matter.status] || '进行中'))
const formatDate = (value) => (value ? String(value).replace('T', ' ').slice(0, 10) : '')
</script>

<style scoped>
.case-header { padding: 0 0 20px; border-bottom: 1px solid var(--color-border); }
.case-heading-row, .case-heading-actions { display: flex; align-items: center; }
.case-heading-row { justify-content: space-between; gap: 20px; }
.case-heading-actions { gap: 14px; }
.case-kicker { margin: 0 0 5px; color: var(--color-text-muted); font-size: 12px; }
h1 { margin: 0; color: var(--color-text); font-size: 23px; line-height: 1.35; font-weight: 650; }
.case-status { padding-left: 10px; border-left: 2px solid var(--color-success); color: var(--color-text-secondary); font-size: 13px; }
.status-closed { border-color: var(--color-info); }
.status-archived { border-color: var(--color-warning); }
.primary-action { min-height: 34px; padding: 0 14px; border: 1px solid var(--color-primary); border-radius: 4px; background: var(--color-primary); color: white; font: inherit; font-size: 13px; cursor: pointer; }
.primary-action:hover { background: var(--color-primary-hover); }
.case-description { max-width: 850px; margin: 12px 0 0; color: var(--color-text-secondary); font-size: 14px; line-height: 1.7; }
.case-meta { display: flex; flex-wrap: wrap; gap: 10px 36px; margin: 18px 0 0; }
.case-meta div { display: flex; gap: 8px; min-width: 130px; font-size: 13px; }
.case-meta dt { color: var(--color-text-muted); }
.case-meta dd { margin: 0; color: var(--color-text); }
@media (max-width: 640px) {
  .case-heading-row { align-items: flex-start; flex-direction: column; gap: 12px; }
  .case-heading-actions { width: 100%; justify-content: space-between; }
  .case-meta { gap: 12px 20px; }
  .case-meta div { min-width: calc(50% - 12px); }
}
</style>
