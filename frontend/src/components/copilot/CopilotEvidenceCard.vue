<template>
  <div class="cop-evidence" :class="{ 'is-highlighted': highlighted, 'is-open': expanded }">
    <button
      class="cop-evidence-head"
      type="button"
      :aria-expanded="expanded ? 'true' : 'false'"
      @click="emit('toggle', card.key)"
    >
      <!-- 首行：①②③ 角标 + 法源标题 + 核验状态（与预览的 .ev .eh 同行关系一致） -->
      <span class="cop-evidence-row">
        <span class="cop-evidence-badge" aria-hidden="true">{{ badge }}</span>
        <span class="cop-evidence-title">{{ card.title }}</span>
        <span class="cop-verify" :class="`cop-verify-${card.verification.state}`">
          <svg
            v-if="card.verification.state === 'verified'"
            aria-hidden="true"
            viewBox="0 0 24 24"
            width="12"
            height="12"
            fill="none"
            stroke="currentColor"
            stroke-width="2.4"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M20 6L9 17l-5-5" />
          </svg>
          <svg
            v-else-if="card.verification.state === 'unverified'"
            aria-hidden="true"
            viewBox="0 0 24 24"
            width="12"
            height="12"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
          </svg>
          {{ card.verification.label }}
        </span>
      </span>
      <span v-if="card.citation" class="cop-evidence-citation">{{ card.citation }}</span>
      <span class="cop-evidence-meta">
        <span>{{ sourceLabel }}</span>
        <span v-if="card.version">版本 {{ card.version }}</span>
        <span v-if="card.sourceStatus">{{ sourceStatusLabel(card.sourceStatus) }}</span>
        <span v-if="card.effectiveDate">生效 {{ card.effectiveDate }}</span>
        <span v-if="card.jurisdiction">{{ card.jurisdiction }}</span>
      </span>
      <span class="cop-evidence-foot">
        <span class="cop-evidence-toggle">{{ expanded ? '收起' : '查看详情' }}</span>
      </span>
    </button>

    <div v-if="expanded" class="cop-evidence-body">
      <!-- 核验结论直接来自响应：未关联法源库条目时显式说明「无法核验」，不用中性文案糊过去 -->
      <p v-if="card.verification.note" class="cop-verify-note" :class="`cop-verify-note-${card.verification.state}`">
        {{ card.verification.note }}
      </p>
      <p v-if="loading" class="cop-note">正在读取条文…</p>
      <ul v-else-if="articles.length" class="cop-articles">
        <li v-for="article in articles" :key="article.id">
          <span class="cop-article-no">{{ article.article_number || article.title || '条文' }}</span>
          <span class="cop-article-text">{{ article.content }}</span>
        </li>
      </ul>
      <p v-else-if="card.linkable" class="cop-note">未取到该法源的条文（尚未入库或读取失败）。</p>
      <p v-else class="cop-note">该引用未带法源库条目 ID，打不开条文原文。</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { sourceStatusLabel } from '../../composables/useLegalWorkspacePresentation'

// 证据卡：props.card 由 composables/copilotLayers.js 的 deriveEvidenceCards 生成
// （形状 A 的引用没有 source_id，卡片据此显示「未关联法源库条目」并禁用条文展开）。
// 展开态与条文数据由父组件持有——useLegalSourceDetail 是模块级单例，同时只展开一张卡最省事。
const props = defineProps({
  card: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
  articles: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  highlighted: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle'])

const sourceLabel = computed(() =>
  props.card.linkable ? `法源库 #${props.card.sourceId}` : '未关联法源库条目',
)

// 角标只是顺序标记（预览里的 ①②③），超过 20 条就退回阿拉伯数字，不硬凑字符
const CIRCLED = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩', '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳']
const badge = computed(() => CIRCLED[props.card.index] || String(props.card.index + 1))
</script>

<style scoped>
.cop-evidence {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  transition: border-color var(--transition-fast), background var(--transition-fast);
}

.cop-evidence.is-open {
  border-color: var(--color-border-hover);
}

/* 逐句溯源命中态：整卡高亮，说明「这条证据支撑当前选中的句子」 */
.cop-evidence.is-highlighted {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
}

.cop-evidence-head {
  display: grid;
  width: 100%;
  gap: 4px;
  padding: 10px 12px;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.cop-evidence-head:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: -2px;
  border-radius: var(--radius-md);
}

.cop-evidence-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.cop-evidence-badge {
  flex: none;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 700;
}

.cop-evidence-title {
  min-width: 0;
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 700;
  word-break: break-word;
}

.cop-evidence-citation {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
  word-break: break-word;
}

.cop-evidence-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.cop-evidence-foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: 2px;
}

/* 核验状态：浅底深字的小胶囊，与 .app-tag 同形；三态各用一套语义色 */
.cop-verify {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  margin-left: auto;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: 700;
  white-space: nowrap;
}

.cop-verify-verified {
  background: var(--color-tag-ok-bg);
  color: var(--color-tag-ok-fg);
}

.cop-verify-unverified {
  background: var(--color-tag-pend-bg);
  color: var(--color-tag-pend-fg);
}

.cop-verify-unknown {
  background: var(--color-bg-alt);
  color: var(--color-text-secondary);
}

.cop-evidence-toggle {
  margin-left: auto;
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-weight: 600;
}

.cop-evidence-body {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}

.cop-verify-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.6;
}

.cop-verify-note-unverified {
  color: var(--color-tag-pend-fg);
}

.cop-verify-note-verified {
  color: var(--color-tag-ok-fg);
}

.cop-verify-note-unknown {
  color: var(--color-text-muted);
}

.cop-articles {
  display: grid;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cop-articles li {
  display: grid;
  gap: 2px;
}

.cop-article-no {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
}

.cop-article-text {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.6;
  word-break: break-word;
}

.cop-note {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}
</style>
