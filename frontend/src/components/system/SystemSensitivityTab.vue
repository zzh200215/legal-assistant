<template>
  <div class="app-section-intro tab-intro">
    <strong>敏感文档治理</strong>
    <span>按敏感级别、权限范围和组织归属查看重点文档，支持抽查治理。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <span class="app-empty-note">查看带敏感级别标注的文档，按级别进行治理和抽查。</span>
        <div class="app-filter-bar-actions">
          <el-button :loading="sensitivityLoading" @click="fetchSensitiveDocuments">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div class="app-metric-grid-3 sensitivity-metric-grid">
    <div class="app-stat">
      <span class="app-stat-label">敏感文档</span>
      <span class="app-stat-value">{{ sensitiveDocuments.length }}<small>份</small></span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">机密</span>
      <span class="app-stat-value">{{ confidentialCount }}<small>份</small></span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">受限</span>
      <span class="app-stat-value">{{ restrictedCount }}<small>份</small></span>
    </div>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>敏感文档列表</h2>
      <span class="app-card-head-meta">共 {{ sensitiveDocuments.length }} 份</span>
    </div>
    <!-- 有数据时表头贴卡片头；空/加载/错误态退回常规内边距，避免状态块贴卡片边 -->
    <div class="app-card-body" :class="{ 'is-flush': sensitiveDocuments.length }">
      <QueryStateView :status="sensitivityStatus" :error="sensitivityError" empty-text="暂无敏感文档" min-height="200px" @retry="fetchSensitiveDocuments">
      <div class="app-table-wrap">
        <el-table v-loading="sensitivityLoading" :data="sensitiveDocuments" border size="small" max-height="520">
          <el-table-column prop="title" label="文档" min-width="180" show-overflow-tooltip />
          <el-table-column prop="classification" label="分类" width="120" />
          <el-table-column prop="sensitivity_level" label="敏感级别" width="120">
            <template #default="{ row }">
              <span class="app-tag" :class="sensitivityTagClass(row.sensitivity_level)">{{ row.sensitivity_level }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="permission_scope" label="权限范围" width="120" />
          <el-table-column prop="organization_id" label="组织 ID" width="90">
            <template #default="{ row }"><span class="app-num">{{ row.organization_id }}</span></template>
          </el-table-column>
          <el-table-column prop="department_id" label="部门 ID" width="90">
            <template #default="{ row }"><span class="app-num">{{ row.department_id }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button text type="primary" @click="openDocument(row)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      </QueryStateView>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemKnowledge } from '../../composables/useSystemKnowledge'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

defineProps({
  openDocument: { type: Function, default: null },
})

const authStore = useAuthStore()

const { sensitivityLoading, sensitivityError, sensitiveDocuments, fetchSensitiveDocuments } = useSystemKnowledge({ client: api, message: ElMessage })

// 敏感级别 → .app-tag 语义变体（方向 B 的标签只用浅底深字，不用 Element type 色板）
const sensitivityTagClass = (level) => (level === 'confidential' ? 'is-danger' : level === 'restricted' ? 'is-pending' : 'is-info')

const confidentialCount = computed(() => sensitiveDocuments.value.filter((item) => item.sensitivity_level === 'confidential').length)
const restrictedCount = computed(() => sensitiveDocuments.value.filter((item) => item.sensitivity_level === 'restricted').length)

const sensitivityStatus = computed(() => {
  if (sensitivityLoading.value && !sensitiveDocuments.value.length) return 'loading'
  if (sensitivityError.value) return 'error'
  if (!sensitiveDocuments.value.length) return 'empty'
  return 'success'
})

onMounted(async () => {
  await authStore.loadMe()
  fetchSensitiveDocuments()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.sensitivity-metric-grid {
  margin-top: var(--space-4);
}
</style>
