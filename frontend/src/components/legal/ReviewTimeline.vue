<template>
  <ol class="review-timeline">
    <li v-for="entry in entries" :key="entry.id || `${entry.action}-${entry.created_at}`" class="review-entry">
      <span class="timeline-marker" :class="`action-${entry.action}`" aria-hidden="true"></span>
      <div class="review-entry-content">
        <div class="review-entry-heading"><strong>{{ actionLabel(entry.action) }}</strong><time>{{ formatDate(entry.created_at) }}</time></div>
        <p v-if="entry.note">{{ entry.note }}</p>
        <span v-if="entry.actor_name" class="review-actor">{{ entry.actor_name }}</span>
      </div>
    </li>
    <li v-if="!entries.length" class="review-empty">暂无审核记录</li>
  </ol>
</template>

<script setup>
import { actionLabel } from '../../composables/useLegalWorkspacePresentation'

defineProps({ entries: { type: Array, default: () => [] } })
const formatDate = (value) => (value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录')
</script>

<style scoped>
.review-timeline { display: grid; margin: 0; padding: 0; list-style: none; }
.review-entry { display: grid; grid-template-columns: 16px minmax(0, 1fr); gap: 10px; padding-bottom: 16px; }
.timeline-marker { position: relative; width: 8px; height: 8px; margin: 5px 0 0 3px; border: 1px solid var(--color-primary); border-radius: 50%; background: #fff; }
.review-entry:not(:last-child) .timeline-marker::after { position: absolute; top: 8px; left: 2px; width: 1px; height: 30px; background: var(--color-border); content: ''; }
.action-approve { border-color: var(--color-success); }
.action-return { border-color: var(--color-warning); }
.action-offline, .action-close { border-color: var(--color-info); }
.review-entry-content { display: grid; gap: 4px; }
.review-entry-heading { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; }
.review-entry-heading strong { font-weight: 600; }
.review-entry-heading time, .review-actor, .review-empty { color: var(--color-text-muted); font-size: 11px; }
.review-entry p { margin: 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.55; }
</style>
