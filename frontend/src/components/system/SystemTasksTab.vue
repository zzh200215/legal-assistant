<template>
  <div class="app-section-intro tab-intro">
    <strong>异步任务与 Agent 运行</strong>
    <span>按来源和状态查看后台任务，支持进入目标对象、查看详情和重试失败任务。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <span>统计周期：</span>
      <el-radio-group v-model="taskDays" @change="resetTaskRunPagination">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
        <el-radio-button :value="90">近 90 天</el-radio-button>
      </el-radio-group>
      <template v-if="isAdmin">
        <span style="margin-left: 12px">范围：</span>
        <el-radio-group v-model="taskScope" @change="resetTaskRunPagination">
          <el-radio-button value="mine">我的</el-radio-button>
          <el-radio-button value="all">全部用户</el-radio-button>
        </el-radio-group>
      </template>
      <span style="margin-left: 12px">来源：</span>
      <el-select v-model="taskSource" clearable placeholder="全部" style="width: 140px" @change="resetTaskRunPagination">
        <el-option label="异步任务" value="async_task" />
        <el-option label="Agent" value="agent" />
      </el-select>
      <span>状态：</span>
      <el-select v-model="taskStatus" clearable placeholder="全部" style="width: 140px" @change="resetTaskRunPagination">
        <el-option label="排队中" value="pending" />
        <el-option label="执行中" value="running" />
        <el-option label="已完成" value="succeeded" />
        <el-option label="失败" value="failed" />
      </el-select>
      <div class="app-toolbar-right">
        <el-button @click="fetchTaskRuns">刷新</el-button>
      </div>
    </div>
  </el-card>

  <el-row :gutter="16" style="margin-bottom: 16px">
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="任务总数" :value="taskRuns.length" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="执行中" :value="runningTaskCount" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="失败任务" :value="failedTaskCount" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="可重试" :value="retryableTaskCount" />
      </el-card>
    </el-col>
  </el-row>

  <el-card class="system-panel-card">
    <template #header>任务列表</template>
    <el-table :data="taskRuns" v-loading="taskLoading" border size="small" max-height="560">
      <el-table-column prop="source" label="来源" width="100">
        <template #default="{ row }">
          <el-tag :type="row.source === 'agent' ? 'danger' : 'warning'" size="small">
            {{ row.source === 'agent' ? 'Agent' : '异步任务' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="任务类型" width="120" />
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <StatusTag kind="task_run" :status="row.status" size="small" />
        </template>
      </el-table-column>
      <el-table-column prop="target_type" label="目标类型" width="100" />
      <el-table-column prop="target_id" label="目标 ID" width="90" />
      <el-table-column prop="message" label="详情" show-overflow-tooltip />
      <el-table-column prop="updated_at" label="最近更新时间" width="180" />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-space wrap>
            <el-button text type="primary" @click="openTaskTarget(row)">查看</el-button>
            <el-button
              v-if="row.retryable"
              text
              type="warning"
              :loading="retryingTaskKey === `${row.source}:${row.task_key}`"
              @click="retryTask(row)"
            >
              重试
            </el-button>
            <el-button text @click="showTaskDetail(row)">详情</el-button>
          </el-space>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      background
      layout="total, prev, pager, next"
      :current-page="taskRunPage"
      :page-size="taskRunPageSize"
      :total="taskRunTotal"
      class="app-pagination-end"
      @current-change="handleTaskRunPageChange"
    />
  </el-card>

  <el-dialog v-model="taskDetailVisible" title="任务详情" width="760px" class="system-dialog" append-to-body>
    <div v-if="selectedTaskDetail">
      <div class="dialog-metrics">
        <div class="dialog-metric">
          <span>来源</span>
          <strong>{{ selectedTaskDetail.source }}</strong>
          <span>{{ selectedTaskDetail.title }}</span>
        </div>
        <div class="dialog-metric">
          <span>状态</span>
          <strong>{{ getStatusLabel('task_run', selectedTaskDetail.status) }}</strong>
          <span>{{ selectedTaskDetail.updated_at || '-' }}</span>
        </div>
        <div class="dialog-metric">
          <span>目标</span>
          <strong>{{ selectedTaskDetail.target_type }}</strong>
          <span>{{ selectedTaskDetail.target_id || '-' }}</span>
        </div>
      </div>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="来源">{{ selectedTaskDetail.source }}</el-descriptions-item>
        <el-descriptions-item label="任务类型">{{ selectedTaskDetail.title }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ getStatusLabel('task_run', selectedTaskDetail.status) }}</el-descriptions-item>
        <el-descriptions-item label="目标">{{ selectedTaskDetail.target_type }} / {{ selectedTaskDetail.target_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="任务 Key">{{ selectedTaskDetail.task_key }}</el-descriptions-item>
        <el-descriptions-item label="最近更新时间">{{ selectedTaskDetail.updated_at || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div class="dialog-section">
        <strong>消息：</strong>
        <div class="dialog-pre">{{ selectedTaskDetail.message || '无' }}</div>
      </div>
      <div v-if="selectedTaskDetail.error" class="dialog-section">
        <strong>错误信息：</strong>
        <div class="dialog-pre dialog-error">{{ selectedTaskDetail.error }}</div>
      </div>
      <div v-if="selectedTaskDetail.goal" class="dialog-section">
        <strong>执行目标：</strong>
        <div class="dialog-pre">{{ selectedTaskDetail.goal }}</div>
      </div>
      <div v-if="selectedTaskDetail.events?.length" class="dialog-section">
        <strong>事件记录：</strong>
        <el-table :data="selectedTaskDetail.events" border size="small" max-height="240" class="dialog-table">
          <el-table-column prop="action" label="事件" width="220" />
          <el-table-column prop="detail" label="详情" show-overflow-tooltip />
          <el-table-column prop="created_at" label="时间" width="180" />
        </el-table>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElSpace } from 'element-plus/es/components/space/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/space/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import StatusTag from '../StatusTag.vue'
import { getStatusLabel } from '../../utils/status'
import { useSystemTaskMonitor } from '../../composables/useSystemTaskMonitor'
import { useSystemActivity } from '../../composables/useSystemActivity'
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const { fetchAlerts } = useSystemActivity({ client: api, message: ElMessage, isAdmin })

const {
  taskDays, taskScope, taskSource, taskStatus, taskRuns, taskLoading, taskRunPage, taskRunPageSize,
  taskRunTotal, retryingTaskKey, taskDetailVisible, selectedTaskDetail, runningTaskCount,
  failedTaskCount, retryableTaskCount, fetchTaskRuns, resetTaskRunPagination, handleTaskRunPageChange,
  openTaskTarget, retryTask, showTaskDetail,
} = useSystemTaskMonitor({ client: api, message: ElMessage, router, isAdmin, onTaskRetried: () => fetchAlerts() })

onMounted(async () => {
  await authStore.loadMe()
  fetchTaskRuns()
})

watch(
  () => route.query.tab,
  async (value, oldValue) => {
    if (value === oldValue) return
    if (value === 'tasks') await fetchTaskRuns()
  }
)
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.dialog-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}
.dialog-metric {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  background: #ffffff;
  border: 1px solid var(--color-border-light);
  display: grid;
  gap: var(--space-1);
}
.dialog-metric span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.dialog-metric strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}
.dialog-section,
.dialog-stack {
  display: grid;
  gap: var(--space-2);
}
.dialog-section {
  margin-top: var(--space-4);
}
.dialog-pre {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--color-text);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
}
.dialog-error {
  color: var(--color-danger);
  background: var(--color-danger-light);
}
.dialog-table {
  margin-top: var(--space-2);
}
.system-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
@media (max-width: 1100px) {
  .dialog-metrics {
    grid-template-columns: 1fr;
  }
}
</style>
