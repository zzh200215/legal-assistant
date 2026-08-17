<template>
  <div class="app-section-intro tab-intro">
    <strong>敏感文档治理</strong>
    <span>按敏感级别、权限范围和组织归属查看重点文档，支持抽查治理。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <div class="app-empty-note">查看带敏感级别标注的文档，按级别进行治理和抽查。</div>
      <el-button :loading="sensitivityLoading" @click="fetchSensitiveDocuments">刷新</el-button>
    </div>
  </el-card>

  <el-card class="system-panel-card">
    <template #header>敏感文档列表</template>
    <el-table :data="sensitiveDocuments" v-loading="sensitivityLoading" border size="small" max-height="520">
      <el-table-column prop="title" label="文档" min-width="180" show-overflow-tooltip />
      <el-table-column prop="classification" label="分类" width="120" />
      <el-table-column prop="sensitivity_level" label="敏感级别" width="120">
        <template #default="{ row }">
          <el-tag :type="row.sensitivity_level === 'confidential' ? 'danger' : row.sensitivity_level === 'restricted' ? 'warning' : 'info'" size="small">
            {{ row.sensitivity_level }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="permission_scope" label="权限范围" width="120" />
      <el-table-column prop="organization_id" label="组织 ID" width="90" />
      <el-table-column prop="department_id" label="部门 ID" width="90" />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button text type="primary" @click="openDocument(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemKnowledge } from '../../composables/useSystemKnowledge'
import { useAuthStore } from '../../stores/auth'

defineProps({
  openDocument: { type: Function, default: null },
})

const authStore = useAuthStore()

const { sensitivityLoading, sensitiveDocuments, fetchSensitiveDocuments } = useSystemKnowledge({ client: api, message: ElMessage })

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
</style>
