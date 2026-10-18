<template>
  <header class="case-header">
    <div class="case-head-row app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">{{ matter.title }}</h1>
        <p class="app-page-sub">案件 #{{ matter.id }} · {{ caseTypeLabel }}</p>
      </div>
      <div class="app-page-head-actions">
        <span class="app-tag" :class="statusTagClass">{{ statusLabel }}</span>
        <button class="primary-action" type="button" @click="$emit('continue')">继续处理</button>
      </div>
    </div>
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
// 状态用胶囊标签表达（进行中=绿、归档=琥珀），不再用左侧竖线；行动按钮保持唯一主色
const statusTagClass = computed(() => ({ in_progress: 'is-ok', archived: 'is-pending' }[props.matter.status] || ''))
const formatDate = (value) => (value ? String(value).replace('T', ' ').slice(0, 10) : '')
</script>

<style scoped>
/* 案件标题区：案情摘要不在这里重复，它属于「案件概览 · 案情摘要」卡片 */
.case-header {
  display: grid;
  gap: var(--space-4);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.case-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 32px;
  margin: 0;
}

.case-meta div {
  display: flex;
  gap: 6px;
  font-size: var(--text-sm);
}

.case-meta dt {
  color: var(--color-text-muted);
}

.case-meta dd {
  margin: 0;
  color: var(--color-text);
  font-weight: 500;
}

.primary-action {
  padding: 9px 15px;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: var(--color-text-inverse);
  font: inherit;
  font-size: var(--text-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast);
}

.primary-action:hover {
  background: var(--color-primary-hover);
}

@media (max-width: 640px) {
  .app-page-head-actions {
    width: 100%;
  }

  .case-meta {
    gap: 10px 20px;
  }

  .case-meta div {
    min-width: calc(50% - 10px);
  }
}
</style>
