<template>
  <div class="documents-page">
    <!-- 案件上下文：方向 B 用面包屑表达「从哪来、在哪」，不再单独铺一条横幅 -->
    <nav v-if="route.query.case_id" class="matter-document-context app-crumbs" aria-label="当前位置">
      <button type="button" class="app-crumbs-link" @click="backToMatter">返回案件</button>
      <span class="app-crumbs-sep" aria-hidden="true">/</span>
      <span class="app-crumbs-current">当前案件 · 文档</span>
      <span class="app-tag is-info">案件 {{ route.query.case_id }}</span>
    </nav>

    <!-- 页头：.page-header 是 e2e 定位 h1「文档」的锚点，必须保留；版式交给方向 B 的 .app-page-head -->
    <header class="page-header app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">文档</h1>
        <p class="app-page-sub">集中查看案件材料、合同、证据和法源文档。</p>
      </div>
      <div class="app-page-head-actions">
        <!-- 当前文档处理状态：三态只换标签语义色，不铺彩色面 -->
        <span v-if="docMeta" class="app-tag" :class="documentStatus.tone">{{ documentStatus.label }}</span>
        <el-button type="primary" :loading="uploading" :disabled="!selectedFiles.length" @click="uploadAndAnalyze">
          {{ selectedFiles.length > 1 ? '批量上传并分析' : '上传并分析' }}
        </el-button>
      </div>
    </header>

    <!-- 数据摘要：原来的文字条 + 竖向分隔线改为 .app-stat 四宫格，读数优先 -->
    <section class="app-metric-grid-4 documents-brief" aria-label="文档摘要">
      <div class="app-stat">
        <span class="app-stat-label">资料总数</span>
        <div class="app-stat-value">{{ materialCount }}<small>份</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">待处理风险</span>
        <div class="app-stat-value">{{ riskCount }}<span v-if="riskCount" class="app-tag is-danger">需处理</span><small v-else>暂无</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">待办事项</span>
        <div class="app-stat-value">{{ todoCount }}<span v-if="todoCount" class="app-tag is-pending">待跟进</span><small v-else>暂无</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">问答记录</span>
        <div class="app-stat-value">{{ qaRecords.length }}<small>条</small></div>
      </div>
    </section>
    <p v-if="!riskCount && !todoCount && !qaRecords.length" class="app-empty-note">选择一份资料查看摘要、风险与引用。</p>

    <!-- 上传入口：原页头右栏的拖拽区收进 .app-card，主按钮移到页头动作区，流程与幂等键不变 -->
    <section class="app-card upload-card">
      <div class="app-card-head">
        <h2>添加法律资料</h2>
        <span class="app-card-head-meta">{{ selectedFiles.length ? `已选 ${selectedFiles.length} 份` : '文件不会自动上传，确认后进入解析队列' }}</span>
      </div>
      <div class="app-card-body">
        <el-upload
          class="upload-dropzone"
          drag
          multiple
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onFileChange"
        >
          <div class="upload-dropzone-inner">
            <strong>拖拽文件到此处，或点击选择</strong>
            <span>支持法规、案例、合同、证据和文书模板</span>
          </div>
        </el-upload>
      </div>
    </section>

    <!-- 主体两栏：目录 + 工作区（两个子组件自身已是卡片，这里只负责两栏骨架） -->
    <div class="layout-grid">
      <DocumentSidebar />
      <DocumentWorkspace />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/upload/style/css'
import DocumentSidebar from '../components/documents/DocumentSidebar.vue'
import DocumentWorkspace from '../components/documents/DocumentWorkspace.vue'
import { useDocuments } from '../composables/useDocuments'

const route = useRoute()
const router = useRouter()
const {
  selectedFiles, uploading, onFileChange, uploadAndAnalyze,
  documentTotal, documents, analysis, docMeta, qaRecords,
  initialize, loadDocumentFromRoute, clearAnalysisPolling,
} = useDocuments()

// 数据摘要由既有响应式状态派生，不新增任何接口调用
const materialCount = computed(() => documentTotal.value || documents.value.length)
const riskCount = computed(() => analysis.value?.risks?.length || 0)
const todoCount = computed(() => analysis.value?.todos?.length || 0)

// 文档处理状态 → .app-tag 三态（完成 / 处理中 / 失败），颜色一律取令牌里的语义色
const DOC_STATUS_TAGS = {
  completed: { label: '已完成', tone: 'is-ok' },
  succeeded: { label: '已完成', tone: 'is-ok' },
  processing: { label: '解析中', tone: 'is-pending' },
  pending: { label: '待处理', tone: 'is-pending' },
  uploading: { label: '上传中', tone: 'is-pending' },
  failed: { label: '解析失败', tone: 'is-danger' },
  error: { label: '解析失败', tone: 'is-danger' },
}
const documentStatus = computed(() => {
  const status = docMeta.value?.status
  return DOC_STATUS_TAGS[status] || { label: status || '未处理', tone: '' }
})

// 「返回案件」保持既有跳转目标：案件工作台的文档页签
const backToMatter = () => {
  router.push({ path: '/legal-workspace', query: { case_id: route.query.case_id, tab: 'documents' } })
}

onMounted(async () => {
  await initialize(route.query.documentId, route.query.case_id)
})

watch(
  () => route.query.documentId,
  async (value, oldValue) => {
    if (value === oldValue) return
    await loadDocumentFromRoute(value)
  }
)

onUnmounted(() => {
  clearAnalysisPolling()
})
</script>

<style scoped>
.documents-page {
  display: grid;
  gap: var(--space-6);
  max-width: 1600px;
}
/* 保留 .page-header 类名（e2e 依赖它定位 h1），但去掉全局 .page-header 的白面与边框：
   方向 B 的页头直接坐在页面底色上，层次交给下面的卡片 */
.page-header.app-page-head {
  padding: 0;
  border: 0;
  border-radius: 0;
  background: none;
}
.page-header.app-page-head .app-page-sub {
  margin: 0;
  max-width: 720px;
  line-height: 1.6;
}
/* 案件上下文：面包屑形态，窄屏允许换行 */
.matter-document-context {
  flex-wrap: wrap;
}
.upload-card .app-card-body {
  display: grid;
  gap: var(--space-2);
}
.upload-dropzone {
  width: 100%;
}
:deep(.upload-dropzone .el-upload) {
  width: 100%;
}
:deep(.upload-dropzone .el-upload-dragger) {
  width: 100%;
  height: 120px;
  border-radius: var(--radius-lg);
  border-color: var(--color-border-hover);
  background: var(--color-surface-subtle);
  padding: 0;
  transition: border-color var(--transition-fast), background var(--transition-fast);
}
/* 拖拽区不加彩色底与外发光环：整块蓝面是页面里最抢眼的“模板色块” */
:deep(.upload-dropzone .el-upload-dragger:hover) {
  border-color: var(--color-primary);
  background: var(--color-bg);
}
.upload-dropzone-inner {
  height: 100%;
  display: grid;
  place-content: center;
  gap: var(--space-1);
  text-align: center;
}
.upload-dropzone-inner strong {
  color: var(--color-text);
  font-size: var(--text-base);
  font-weight: 500;
}
.upload-dropzone-inner span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.layout-grid {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: var(--space-6);
  align-items: start;
}
@media (max-width: 1100px) {
  .layout-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 640px) {
  .app-page-head-actions {
    width: 100%;
    margin-left: 0;
  }
  .app-page-head-actions .el-button {
    flex: 1;
  }
}
</style>
