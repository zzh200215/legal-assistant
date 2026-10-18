<template>
  <div class="app-section-intro tab-intro">
    <strong>异步任务与 Agent 运行</strong>
    <span>按来源和状态查看后台任务，支持进入目标对象、查看详情和重试失败任务。</span>
  </div>

  <WorkflowOperationsPanel />

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <span class="app-filter-label">统计周期：</span>
        <el-radio-group v-model="taskDays" @change="resetTaskRunPagination">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <template v-if="isAdmin">
          <span class="app-filter-label">范围：</span>
          <el-radio-group v-model="taskScope" @change="resetTaskRunPagination">
            <el-radio-button value="mine">我的</el-radio-button>
            <el-radio-button value="all">全部用户</el-radio-button>
          </el-radio-group>
        </template>
        <span class="app-filter-label">来源：</span>
        <el-select v-model="taskSource" clearable placeholder="全部" class="system-filter-select" @change="resetTaskRunPagination">
          <el-option label="异步任务" value="async_task" />
          <el-option label="Agent" value="agent" />
        </el-select>
        <span class="app-filter-label">状态：</span>
        <el-select v-model="taskStatus" clearable placeholder="全部" class="system-filter-select" @change="resetTaskRunPagination">
          <el-option label="排队中" value="pending" />
          <el-option label="执行中" value="running" />
          <el-option label="已完成" value="succeeded" />
          <el-option label="失败" value="failed" />
        </el-select>
        <div class="app-filter-bar-actions">
          <el-button @click="fetchTaskRuns">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div class="app-metric-grid-4 task-metric-grid">
    <div class="app-stat">
      <span class="app-stat-label">任务总数</span>
      <strong class="app-stat-value">{{ taskRuns.length }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">执行中</span>
      <strong class="app-stat-value">{{ runningTaskCount }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">失败任务</span>
      <strong class="app-stat-value">{{ failedTaskCount }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">可重试</span>
      <strong class="app-stat-value">{{ retryableTaskCount }}</strong>
    </div>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>任务列表</h2>
      <span class="app-card-head-meta">共 {{ taskRunTotal }} 条</span>
    </div>
    <div class="app-card-body">
      <div class="app-table-wrap">
      <QueryStateView :status="taskRunsStatus" :error="taskError" empty-text="暂无任务运行记录" min-height="200px" @retry="fetchTaskRuns">
      <el-table v-loading="taskLoading" :data="taskRuns" border size="small" max-height="560">
        <el-table-column prop="source" label="来源" width="100">
          <template #default="{ row }">
            <span class="app-tag" :class="row.source === 'agent' ? 'is-accent' : 'is-info'">
              {{ row.source === 'agent' ? 'Agent' : '异步任务' }}
            </span>
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
      </QueryStateView>
      </div>
      <el-pagination
        background
        layout="total, prev, pager, next"
        :current-page="taskRunPage"
        :page-size="taskRunPageSize"
        :total="taskRunTotal"
        class="app-pagination-end"
        @current-change="handleTaskRunPageChange"
      />
    </div>
  </section>

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
      <dl class="dialog-kv">
        <div><dt>来源</dt><dd>{{ selectedTaskDetail.source }}</dd></div>
        <div><dt>任务类型</dt><dd>{{ selectedTaskDetail.title }}</dd></div>
        <div><dt>状态</dt><dd>{{ getStatusLabel('task_run', selectedTaskDetail.status) }}</dd></div>
        <div><dt>目标</dt><dd>{{ selectedTaskDetail.target_type }} / {{ selectedTaskDetail.target_id || '-' }}</dd></div>
        <div><dt>任务 Key</dt><dd class="app-num">{{ selectedTaskDetail.task_key }}</dd></div>
        <div><dt>最近更新时间</dt><dd class="app-num">{{ selectedTaskDetail.updated_at || '-' }}</dd></div>
      </dl>
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
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElSpace } from 'element-plus/es/components/space/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/space/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import StatusTag from '../StatusTag.vue'
import { getStatusLabel } from '../../utils/status'
import { useSystemTaskMonitor } from '../../composables/useSystemTaskMonitor'
import { useSystemActivity } from '../../composables/useSystemActivity'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'
import WorkflowOperationsPanel from './WorkflowOperationsPanel.vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const { fetchAlerts } = useSystemActivity({ client: api, message: ElMessage, isAdmin })

const {
  taskDays, taskScope, taskSource, taskStatus, taskRuns, taskLoading, taskError, taskRunPage, taskRunPageSize,
  taskRunTotal, retryingTaskKey, taskDetailVisible, selectedTaskDetail, runningTaskCount,
  failedTaskCount, retryableTaskCount, fetchTaskRuns, resetTaskRunPagination, handleTaskRunPageChange,
  openTaskTarget, retryTask, showTaskDetail,
} = useSystemTaskMonitor({ client: api, message: ElMessage, router, isAdmin, onTaskRetried: () => fetchAlerts() })

const taskRunsStatus = computed(() => {
  if (taskLoading.value && !taskRuns.value.length) return 'loading'
  if (taskError.value) return 'error'
  if (!taskRuns.value.length) return 'empty'
  return 'success'
})

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
.task-metric-grid {
  margin-top: var(--space-4);
}
.system-filter-select {
  width: 140px;
}
.dialog-kv {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2) var(--space-5);
  margin: 0;
}
.dialog-kv div {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: var(--space-3);
  align-items: baseline;
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border-light);
}
.dialog-kv dt {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.dialog-kv dd {
  margin: 0;
  color: var(--color-text);
  font-size: var(--text-sm);
  word-break: break-all;
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
  background: var(--color-surface);
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
  .dialog-kv {
    grid-template-columns: 1fr;
  }
}
</style>
