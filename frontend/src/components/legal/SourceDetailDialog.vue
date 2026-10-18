<template>
  <!-- 引用依据核对：工作台各 tab 的引用点击出口。
       助手面板（copilot）走卡片内联展开，这里只渲染 surface === 'legal' 的那次打开。 -->
  <el-dialog v-model="visible" title="引用依据核对" width="720px" append-to-body>
    <div v-if="detail" class="source-detail">
      <header class="source-detail-head">
        <h3 class="source-detail-title">{{ detail.title || '未命名法源' }}</h3>
        <p v-if="detail.citation" class="source-detail-citation">{{ detail.citation }}</p>
        <div class="source-detail-meta">
          <span v-if="detail.status" class="app-tag" :class="statusTagClass(detail.status)">{{ sourceStatusLabel(detail.status) }}</span>
          <span v-else-if="!detail.source_id" class="app-tag is-pending">未关联法源库条目</span>
          <span v-if="detail.version" class="source-detail-version">版本 {{ detail.version }}</span>
          <span v-if="detail.effective_date">施行日期 {{ detail.effective_date }}</span>
          <span v-if="detail.jurisdiction">{{ detail.jurisdiction }}</span>
        </div>
      </header>
      <section class="source-detail-body">
        <p v-if="loading" class="source-detail-note">正在读取条文…</p>
        <ul v-else-if="articles.length" class="article-list">
          <li v-for="article in articles" :key="article.id">
            <span class="article-no">{{ article.article_number || article.title || '条文' }}</span>
            <span class="article-text">{{ article.content }}</span>
          </li>
        </ul>
        <p v-else-if="detail.source_id" class="source-detail-note">未取到该法源的条文（尚未入库或读取失败）。</p>
        <p v-else class="source-detail-note">该引用未带法源库条目 ID，打不开条文原文。</p>
      </section>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed } from 'vue'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import 'element-plus/es/components/dialog/style/css'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'
import { sourceStatusLabel } from '../../composables/useLegalWorkspacePresentation'

const { sourceDetailVisible, sourceDetail, sourceDetailArticles, sourceDetailLoading, sourceDetailSurface } = useLegalSourceDetail()

const visible = computed({
  get: () => sourceDetailVisible.value && sourceDetailSurface.value === 'legal',
  set: (value) => { sourceDetailVisible.value = value },
})
const detail = computed(() => sourceDetail.value)
const articles = computed(() => sourceDetailArticles.value)
const loading = computed(() => sourceDetailLoading.value)
const statusTagClass = (status) => ({ active: 'is-ok', pending_update: 'is-pending', inactive: 'is-info' }[status] || '')
</script>

<style scoped>
.source-detail { display: grid; gap: var(--space-4); }
.source-detail-head { display: grid; gap: var(--space-2); }
.source-detail-title { margin: 0; font-size: var(--text-lg); font-weight: 650; color: var(--color-text); }
.source-detail-citation { margin: 0; font-size: var(--text-sm); line-height: 1.7; color: var(--color-text-secondary); }
.source-detail-meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); font-size: var(--text-xs); color: var(--color-text-muted); }
.source-detail-version { font-family: var(--font-mono); }
.source-detail-body { border-top: 1px solid var(--color-border-light); padding-top: var(--space-4); }
.source-detail-note { margin: 0; font-size: var(--text-sm); color: var(--color-text-muted); }
.article-list { display: grid; gap: var(--space-3); margin: 0; padding: 0; list-style: none; max-height: 420px; overflow-y: auto; }
.article-list li { display: grid; gap: var(--space-1); padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.article-no { font-size: var(--text-sm); font-weight: 600; color: var(--color-primary); }
.article-text { font-size: var(--text-sm); line-height: 1.75; color: var(--color-text-secondary); }
</style>
