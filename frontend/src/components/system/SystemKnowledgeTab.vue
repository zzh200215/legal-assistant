<template>
  <div class="app-section-intro tab-intro">
    <strong>知识库与入库状态</strong>
    <span>查看知识库空间、权限范围、文档分类情况和最近入库内容。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <span class="app-empty-note">查看知识库空间、权限范围和最近入库文档。</span>
        <div class="app-filter-bar-actions">
          <el-button :loading="knowledgeLoading" @click="fetchKnowledgeData">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div class="app-metric-grid-3 knowledge-metric-grid">
    <div class="app-stat">
      <span class="app-stat-label">知识库数量</span>
      <span class="app-stat-value">{{ knowledgeBases.length }}<small>个</small></span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">文档数量</span>
      <span class="app-stat-value">{{ knowledgeDocuments.length }}<small>份</small></span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">已分类文档</span>
      <span class="app-stat-value">{{ classifiedDocumentCount }}<small>份</small></span>
    </div>
  </div>

  <div class="app-panel-grid-2 knowledge-tables-grid">
    <section class="app-card">
      <div class="app-card-head">
        <h2>知识库列表</h2>
        <span class="app-card-head-meta">共 {{ knowledgeBases.length }} 个</span>
      </div>
      <!-- 有数据时表头贴卡片头；空/加载/错误态退回常规内边距，避免状态块贴卡片边 -->
      <div class="app-card-body" :class="{ 'is-flush': knowledgeBases.length }">
        <QueryStateView :status="knowledgeStatus" :error="knowledgeError" empty-text="暂无知识库数据" min-height="200px" @retry="fetchKnowledgeData">
        <div class="app-table-wrap">
          <el-table v-loading="knowledgeLoading" :data="knowledgeBases" border size="small" max-height="420">
            <el-table-column prop="name" label="名称" min-width="140" />
            <el-table-column prop="category" label="分类" width="100" />
            <el-table-column prop="permission_scope" label="权限" width="100" />
            <el-table-column prop="created_at" label="创建时间" width="180" />
          </el-table>
        </div>
        </QueryStateView>
      </div>
    </section>

    <section class="app-card">
      <div class="app-card-head">
        <h2>最近文档</h2>
        <span class="app-card-head-meta">共 {{ knowledgeDocuments.length }} 份</span>
      </div>
      <div class="app-card-body" :class="{ 'is-flush': knowledgeDocuments.length }">
        <div class="app-table-wrap">
          <el-table v-loading="knowledgeLoading" :data="knowledgeDocuments" border size="small" max-height="420">
            <el-table-column prop="title" label="文档" min-width="180" show-overflow-tooltip />
            <el-table-column prop="knowledge_base_id" label="知识库 ID" width="100">
              <template #default="{ row }"><span class="app-num">{{ row.knowledge_base_id }}</span></template>
            </el-table-column>
            <el-table-column prop="classification" label="分类" width="100" />
            <el-table-column prop="version_number" label="版本" width="80">
              <template #default="{ row }"><span class="app-num">{{ row.version_number }}</span></template>
            </el-table-column>
            <el-table-column prop="permission_scope" label="权限" width="100" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button text type="primary" @click="openDocument?.(row)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemKnowledge } from '../../composables/useSystemKnowledge'
import QueryStateView from '../QueryStateView.vue'

defineProps({
  openDocument: { type: Function, default: null },
})

const {
  knowledgeLoading,
  knowledgeBases,
  knowledgeDocuments,
  knowledgeError,
  fetchKnowledgeData,
} = useSystemKnowledge({ client: api, message: ElMessage })

const classifiedDocumentCount = computed(() => knowledgeDocuments.value.filter((item) => item.classification).length)

// 三态（ux-audit M-13）：仅包裹主结果表 knowledgeBases；多表共用一次 fetch，保留原有 message.error 以覆盖其它表
const knowledgeStatus = computed(() => {
  if (knowledgeLoading.value && !knowledgeBases.value.length) return 'loading'
  if (knowledgeError.value) return 'error'
  if (!knowledgeBases.value.length) return 'empty'
  return 'success'
})

onMounted(() => {
  fetchKnowledgeData()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.knowledge-metric-grid {
  margin-top: var(--space-4);
}
.knowledge-tables-grid {
  margin-top: var(--space-4);
}
</style>
