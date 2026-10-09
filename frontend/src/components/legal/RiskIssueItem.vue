<template>
  <button class="risk-issue" :class="{ selected: active }" type="button" @click="$emit('select', issue)">
    <span class="risk-topline">
      <span class="risk-level" :class="`level-${issue.risk_level || 'low'}`">{{ riskLabel(issue.risk_level) }}</span>
      <span v-if="issue.source_location?.paragraph" class="risk-location">第 {{ issue.source_location.paragraph }} 段</span>
    </span>
    <strong>{{ issue.label || clauseLabel(issue.clause_type) || '合同风险' }}</strong>
    <span class="risk-description">{{ issue.description || '需要进一步核对该条款。' }}</span>
    <span v-if="issue.source_location?.snippet" class="risk-snippet">“{{ issue.source_location.snippet }}”</span>
  </button>
</template>

<script setup>
import { clauseLabel, riskLabel } from '../../composables/useLegalWorkspacePresentation'

defineProps({ issue: { type: Object, required: true }, active: { type: Boolean, default: false } })
defineEmits(['select'])
</script>

<style scoped>
.risk-issue { display: grid; width: 100%; gap: 8px; padding: 13px 14px; border: 0; border-bottom: 1px solid var(--color-border-light); background: #fff; color: var(--color-text); text-align: left; cursor: pointer; }
.risk-issue:hover { background: #F7F9FB; }
.risk-issue.selected { background: #EEF4F8; box-shadow: inset 3px 0 var(--color-primary); }
.risk-topline { display: flex; align-items: center; gap: 10px; }
.risk-level { padding-left: 8px; border-left: 2px solid var(--color-info); color: var(--color-text-secondary); font-size: 11px; }
.level-high { border-color: var(--color-danger); color: var(--color-danger); }
.level-medium { border-color: var(--color-warning); color: var(--color-warning); }
.level-low { border-color: var(--color-success); color: var(--color-success); }
.risk-location { color: var(--color-text-muted); font-size: 11px; }
strong { font-size: 13px; font-weight: 600; }
.risk-description { color: var(--color-text-secondary); font-size: 12px; line-height: 1.65; }
.risk-snippet { color: var(--color-text-muted); font-size: 11px; line-height: 1.55; }
</style>
