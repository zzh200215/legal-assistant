<template>
        <div class="app-section-intro tab-intro">
          <strong>知识库与入库状态</strong>
          <span>查看知识库空间、权限范围、文档分类情况和最近入库内容。</span>
        </div>

        <el-card class="system-panel-card">
          <div class="app-toolbar">
            <div class="app-empty-note">查看知识库空间、权限范围和最近入库文档。</div>
            <el-button :loading="knowledgeLoading" @click="fetchKnowledgeData">刷新</el-button>
          </div>
        </el-card>

        <el-row :gutter="16" style="margin-bottom: 16px">
          <el-col :span="8">
            <el-card shadow="hover">
              <el-statistic title="知识库数量" :value="knowledgeBases.length" />
            </el-card>
          </el-col>
          <el-col :span="8">
            <el-card shadow="hover">
              <el-statistic title="文档数量" :value="knowledgeDocuments.length" />
            </el-card>
          </el-col>
          <el-col :span="8">
            <el-card shadow="hover">
              <el-statistic title="已分类文档" :value="knowledgeDocuments.filter((item) => item.classification).length" />
            </el-card>
          </el-col>
        </el-row>

        <el-row :gutter="16">
          <el-col :span="10">
            <el-card>
              <template #header>知识库列表</template>
              <el-table :data="knowledgeBases" v-loading="knowledgeLoading" border size="small" max-height="420">
                <el-table-column prop="name" label="名称" min-width="140" />
                <el-table-column prop="category" label="分类" width="100" />
                <el-table-column prop="permission_scope" label="权限" width="100" />
                <el-table-column prop="created_at" label="创建时间" width="180" />
              </el-table>
            </el-card>
          </el-col>
          <el-col :span="14">
            <el-card>
              <template #header>最近文档</template>
              <el-table :data="knowledgeDocuments" v-loading="knowledgeLoading" border size="small" max-height="420">
                <el-table-column prop="title" label="文档" min-width="180" show-overflow-tooltip />
                <el-table-column prop="knowledge_base_id" label="知识库 ID" width="100" />
                <el-table-column prop="classification" label="分类" width="100" />
                <el-table-column prop="version_number" label="版本" width="80" />
                <el-table-column prop="permission_scope" label="权限" width="100" />
                <el-table-column label="操作" width="90" fixed="right">
                  <template #default="{ row }">
                    <el-button text type="primary" @click="openKnowledgeDocument(row)">查看</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>
</template>

<script setup>
import { onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemKnowledge } from '../../composables/useSystemKnowledge'

defineProps({
  openDocument: { type: Function, default: null },
})

const {
  knowledgeLoading,
  knowledgeBases,
  knowledgeDocuments,
  fetchKnowledgeData,
} = useSystemKnowledge({ client: api, message: ElMessage })

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
</style>
