<template>
  <section class="legal-document-viewer" aria-label="合同原文">
    <p v-if="!paragraphs.length" class="document-empty">上传合同文件或粘贴合同原文后开始审查。</p>
    <p
      v-for="(paragraph, index) in paragraphs"
      :id="`para-${index + 1}`"
      :key="index"
      class="document-paragraph"
      :class="{ highlighted: highlightedParagraph === index + 1 }"
      @click="$emit('paragraph-click', index + 1)"
    >
      <span class="paragraph-number">{{ index + 1 }}</span>
      <span><template v-for="(part, partIndex) in paragraphParts(paragraph, index + 1)" :key="partIndex"><mark v-if="part.highlighted">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></span>
    </p>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  content: { type: String, default: '' },
  highlightedParagraph: { type: Number, default: null },
  highlightedRange: { type: Object, default: null },
})
defineEmits(['paragraph-click'])
const paragraphs = computed(() => (props.content || '').split('\n').filter((line) => line.trim()))
const paragraphParts = (text, paragraph) => {
  const range = props.highlightedRange
  if (!range || Number(range.paragraph) !== paragraph || !Number.isInteger(range.start) || !Number.isInteger(range.end) || range.end <= range.start) {
    return [{ text, highlighted: false }]
  }
  const start = Math.max(0, Math.min(text.length, range.start))
  const end = Math.max(start, Math.min(text.length, range.end))
  if (start === end) return [{ text, highlighted: false }]
  return [
    ...(start ? [{ text: text.slice(0, start), highlighted: false }] : []),
    { text: text.slice(start, end), highlighted: true },
    ...(end < text.length ? [{ text: text.slice(end), highlighted: false }] : []),
  ]
}
</script>

<style scoped>
.legal-document-viewer { height: 100%; overflow: auto; padding: 20px clamp(16px, 4vw, 44px); background: #fff; }
.document-empty { max-width: 420px; margin: 24px auto; color: var(--color-text-muted); text-align: center; line-height: 1.7; }
.document-paragraph { display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 12px; margin: 0; padding: 11px 10px; border-bottom: 1px solid #eef1f4; color: #293542; font-family: var(--font-family); font-size: 14px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; scroll-margin: 45vh; }
.paragraph-number { color: #8793a0; font-family: var(--font-mono); font-size: 11px; text-align: right; user-select: none; }
.document-paragraph.highlighted { background: #FFF5D9; box-shadow: inset 3px 0 #C88C25; }
.document-paragraph mark { background: #FFE29A; color: inherit; box-decoration-break: clone; }
@media (max-width: 640px) { .legal-document-viewer { padding: 12px 10px; } .document-paragraph { grid-template-columns: 24px minmax(0,1fr); gap: 8px; font-size: 13px; } }
</style>
