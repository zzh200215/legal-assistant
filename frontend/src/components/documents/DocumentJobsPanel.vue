<template>
  <el-card v-if="parseJobs.length" class="panel-card">
    <template #header>
      <div class="card-header-inline">
        <span>解析任务</span>
        <el-button text @click="$emit('refresh')">刷新</el-button>
      </div>
    </template>
    <el-table :data="parseJobs" border size="small">
      <el-table-column prop="job_type" label="任务类型" width="120">
        <template #default="{ row }">
          {{ parseJobTypeLabel(row.job_type) }}
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="110">
        <template #default="{ row }">
          <StatusTag kind="task_run" :status="row.status" size="small" />
        </template>
      </el-table-column>
      <el-table-column prop="progress" label="进度" width="90">
        <template #default="{ row }">{{ row.progress ?? '-' }}%</template>
      </el-table-column>
      <el-table-column prop="current_step" label="当前步骤" width="140" />
      <el-table-column prop="message" label="说明" show-overflow-tooltip />
      <el-table-column prop="error_message" label="错误" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'failed' && (row.job_type === 'document_parse' || row.job_type === 'document_parse_retry')"
            size="small"
            type="danger"
            text
            @click="$emit('retry')"
          >
            重试解析
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ElCard } from 'element-plus/es/components/card/index'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import StatusTag from '../StatusTag.vue'

defineProps({
  parseJobs: { type: Array, default: () => [] },
})
defineEmits(['retry', 'refresh'])

const parseJobTypeLabel = (value) => ({
  document_parse: '文档解析',
  document_parse_retry: '重新解析',
  document_summary: '文档摘要',
  document_analysis: '文档分析',
}[value] || value || '后台任务')
</script>
