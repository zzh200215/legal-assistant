<template>
  <div class="document-diff" aria-label="文书版本差异">
    <div class="diff-toolbar"><span>Version {{ beforeVersion || '历史' }}</span><span>→</span><strong>{{ afterVersion ? `Version ${afterVersion}` : '当前正文' }}</strong></div>
    <div v-if="!rows.length" class="diff-empty">两个版本内容相同。</div>
    <ol v-else class="diff-lines">
      <li v-for="(row, index) in rows" :key="`${row.kind}-${index}`" class="diff-line" :class="`diff-${row.kind}`">
        <span class="diff-mark">{{ row.kind === 'added' ? '+' : row.kind === 'removed' ? '-' : ' ' }}</span>
        <span>{{ row.text || ' ' }}</span>
      </li>
    </ol>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  before: { type: String, default: '' },
  after: { type: String, default: '' },
  beforeVersion: { type: [String, Number], default: null },
  afterVersion: { type: [String, Number], default: null },
  rows: { type: Array, default: null },
})

const computedRows = computed(() => {
  const before = (props.before || '').split('\n')
  const after = (props.after || '').split('\n')
  const matrix = Array.from({ length: before.length + 1 }, () => Array(after.length + 1).fill(0))
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      matrix[i][j] = before[i] === after[j] ? matrix[i + 1][j + 1] + 1 : Math.max(matrix[i + 1][j], matrix[i][j + 1])
    }
  }
  const result = []
  let i = 0; let j = 0
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) { result.push({ kind: 'same', text: before[i] }); i += 1; j += 1 }
    else if (matrix[i + 1][j] >= matrix[i][j + 1]) { result.push({ kind: 'removed', text: before[i] }); i += 1 }
    else { result.push({ kind: 'added', text: after[j] }); j += 1 }
  }
  while (i < before.length) { result.push({ kind: 'removed', text: before[i] }); i += 1 }
  while (j < after.length) { result.push({ kind: 'added', text: after[j] }); j += 1 }
  return result.filter((row) => row.kind !== 'same' || result.some((item) => item.kind !== 'same'))
})
const rows = computed(() => props.rows || computedRows.value)
</script>

<style scoped>
.document-diff { border: 1px solid var(--color-border); background: #fff; }
.diff-toolbar { display: flex; gap: 8px; padding: 9px 12px; border-bottom: 1px solid var(--color-border-light); color: var(--color-text-muted); font-size: 11px; }
.diff-toolbar strong { color: var(--color-text); font-weight: 600; }
.diff-lines { max-height: 360px; margin: 0; padding: 8px 0; overflow: auto; list-style: none; font-family: var(--font-mono); font-size: 12px; line-height: 1.65; }
.diff-line { display: grid; grid-template-columns: 24px minmax(0, 1fr); padding: 2px 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.diff-mark { color: var(--color-text-muted); user-select: none; }
.diff-added { background: #eef8f0; color: #216b36; }.diff-added .diff-mark { color: #278344; }
.diff-removed { background: #fff1f0; color: #9d3028; }.diff-removed .diff-mark { color: #bd3b32; }
.diff-empty { padding: 14px 12px; color: var(--color-text-muted); font-size: 12px; }
</style>
