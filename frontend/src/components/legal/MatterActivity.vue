<template>
  <ol class="matter-activity">
    <li v-for="item in items" :key="`${item.type}-${item.id}`" class="activity-entry">
      <span class="activity-marker" :class="`marker-${item.type}`" aria-hidden="true"></span>
      <div class="activity-copy">
        <strong>{{ item.title }}</strong>
        <span v-if="item.description">{{ item.description }}</span>
        <time>{{ formatDate(item.created_at) }}</time>
      </div>
    </li>
    <li v-if="!items.length" class="activity-empty">案件尚无工作记录</li>
  </ol>
</template>

<script setup>
defineProps({ items: { type: Array, default: () => [] } })
const formatDate = (value) => (value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录')
</script>

<style scoped>
.matter-activity { display: grid; margin: 0; padding: 0; list-style: none; }
.activity-entry { display: grid; grid-template-columns: 16px minmax(0, 1fr); gap: 10px; padding: 0 0 17px; }
.activity-marker { position: relative; width: 8px; height: 8px; margin: 5px 0 0 3px; border: 1px solid var(--color-primary); border-radius: 50%; background: #fff; }
.activity-entry:not(:last-child) .activity-marker::after { position: absolute; top: 8px; left: 2px; width: 1px; height: 30px; background: var(--color-border); content: ''; }
.marker-contract_review { border-color: var(--color-warning); }
.activity-copy { display: grid; gap: 3px; }
.activity-copy strong { font-size: 13px; font-weight: 550; }
.activity-copy span, .activity-copy time, .activity-empty { color: var(--color-text-muted); font-size: 12px; line-height: 1.5; }
.activity-empty { padding: 4px 0; }
</style>
