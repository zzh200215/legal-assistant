<template>
  <div class="documents-page">
    <div v-if="route.query.case_id" class="matter-document-context">
      <span>当前案件 · 文档</span>
      <button type="button" @click="router.push({ path: '/legal-workspace', query: { case_id: route.query.case_id, tab: 'documents' } })">返回案件</button>
    </div>
    <header class="page-header">
      <div class="page-heading-copy">
        <p class="section-eyebrow">法律资料</p>
        <h1>文档</h1>
        <p>集中查看案件材料、合同、证据和法源文档。</p>
      </div>
      <div class="upload-console">
        <el-upload
          class="upload-dropzone"
          drag
          multiple
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onFileChange"
        >
          <div class="upload-dropzone-inner">
            <strong>添加法律资料</strong>
            <span>支持法规、案例、合同、证据和文书模板</span>
          </div>
        </el-upload>
        <div class="upload-console-foot">
          <span class="toolbar-meta">{{ selectedFiles.length ? `已选 ${selectedFiles.length} 份` : '文件不会自动上传，确认后进入解析队列' }}</span>
          <el-button type="primary" :loading="uploading" :disabled="!selectedFiles.length" @click="uploadAndAnalyze">
            {{ selectedFiles.length > 1 ? '批量上传并分析' : '上传并分析' }}
          </el-button>
        </div>
      </div>
    </header>

    <div class="document-brief" aria-label="文档摘要">
      <span><strong>{{ documentTotal || documents.length }}</strong> 份资料</span>
      <span v-if="analysis?.risks?.length"><strong>{{ analysis.risks.length }}</strong> 个待处理风险</span>
      <span v-if="analysis?.todos?.length"><strong>{{ analysis.todos.length }}</strong> 个待办</span>
      <span v-if="qaRecords.length"><strong>{{ qaRecords.length }}</strong> 条问答记录</span>
      <span v-if="!analysis?.risks?.length && !analysis?.todos?.length && !qaRecords.length" class="brief-muted">选择一份资料查看摘要、风险与引用</span>
    </div>

    <div class="layout-grid">
      <DocumentSidebar />
      <DocumentWorkspace />
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, watch } from 'vue'
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
  documentTotal, documents, analysis, qaRecords,
  initialize, loadDocumentFromRoute, clearAnalysisPolling,
} = useDocuments()

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
.page-header {
  display: grid;
  grid-template-columns: minmax(240px, .7fr) minmax(420px, 1.3fr);
  align-items: end;
  gap: var(--space-6);
  padding: var(--space-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}
.page-header h1 {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text);
  font-size: var(--text-3xl);
  font-weight: 600;
}
.page-header p {
  max-width: 720px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: var(--text-sm);
}
.page-heading-copy { display: grid; gap: var(--space-1); }
.page-heading-copy p:last-child { margin: 0; }
.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}
.document-brief { display: flex; align-items: center; gap: 24px; min-height: 42px; padding: 0 4px; border-bottom: 1px solid var(--color-border); color: var(--color-text-secondary); font-size: var(--text-sm); }
.document-brief span { padding-right: 24px; border-right: 1px solid var(--color-border-light); }
.document-brief span:last-child { border-right: 0; }
.document-brief strong { color: var(--color-text); font-size: var(--text-lg); font-weight: 600; }
.document-brief .brief-muted { color: var(--color-text-muted); }
.upload-console {
  width: 100%;
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
  height: 104px;
  border-radius: var(--radius-md);
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
.upload-console-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.toolbar-meta {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.layout-grid {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: var(--space-6);
}
@media (max-width: 1100px) {
  .page-header,
  .layout-grid {
    grid-template-columns: 1fr;
  }
  .page-header {
    display: grid;
  }
}
@media (max-width: 640px) {
  .document-brief { align-items: flex-start; flex-wrap: wrap; gap: 10px 16px; padding: 8px 4px; }
  .document-brief span { padding-right: 16px; }
}
</style>
