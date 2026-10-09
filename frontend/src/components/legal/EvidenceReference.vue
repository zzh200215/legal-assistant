<template>
  <button class="evidence-reference" type="button" :disabled="!clickable" @click="$emit('open', reference)">
    <span class="evidence-title">{{ reference.title || '法律依据' }}</span>
    <span v-if="reference.citation" class="evidence-citation">{{ reference.citation }}</span>
    <span class="evidence-meta">
      <span v-if="reference.status">{{ sourceStatusLabel(reference.status) }}</span>
      <span v-if="reference.version">版本 {{ reference.version }}</span>
    </span>
  </button>
</template>

<script setup>
import { sourceStatusLabel } from '../../composables/useLegalWorkspacePresentation'

defineProps({ reference: { type: Object, required: true }, clickable: { type: Boolean, default: true } })
defineEmits(['open'])
</script>

<style scoped>
.evidence-reference { display: grid; width: 100%; gap: 4px; padding: 10px 0; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; text-align: left; cursor: pointer; }
.evidence-reference:disabled { cursor: default; }
.evidence-title { color: var(--color-primary); font-size: 12px; font-weight: 600; }
.evidence-citation { color: var(--color-text-secondary); font-size: 12px; line-height: 1.55; }
.evidence-meta { display: flex; gap: 12px; color: var(--color-text-muted); font-size: 11px; }
</style>
