<template>
  <div class="side-panels">
    <!-- 待审批操作：卡片外壳统一走 .app-card，单条审批改证据行 -->
    <section class="app-card history-card">
      <div class="app-card-head">
        <h2>待审批操作</h2>
        <span class="card-head-tail">
          <span v-if="approvals.length" class="head-count">{{ approvals.length }} 条</span>
          <el-button size="small" @click="fetchApprovals">刷新</el-button>
        </span>
      </div>
      <div class="app-card-body">
        <div v-if="approvals.length" class="stack-list">
          <div v-for="item in approvals" :key="`approval-${item.id}`" class="app-evidence">
            <div class="app-evidence-head">
              <strong class="app-evidence-title">{{ toolLabel(item.tool_name) }}</strong>
              <span class="meta-row">
                <span class="app-tag is-pending">{{ item.risk_level }}</span>
                <span class="app-tag" :class="approvalStatusTone(item.status)">{{ approvalStatusLabel(item.status) }}</span>
              </span>
            </div>
            <div class="meta-row">
              <span v-if="item.agent_type">Agent：{{ item.agent_type }}</span>
              <span>{{ item.created_at }}</span>
            </div>
            <el-input type="textarea" :rows="3" :model-value="formatJson(item.input_params)" readonly />
            <div v-if="item.status === 'pending'" class="meta-row">
              <el-button size="small" type="success" @click="decideApproval(item, true)">通过</el-button>
              <el-button size="small" type="danger" plain @click="decideApproval(item, false)">拒绝</el-button>
            </div>
            <div v-else-if="item.decision_note" class="app-evidence-text">备注：{{ item.decision_note }}</div>
          </div>
        </div>
        <el-empty v-else description="暂无待审批操作" />
      </div>
    </section>

    <!-- 运行历史：表格 / 分页 / 空态保持 Element Plus 组件与交互，只换外壳与间距 -->
    <section class="app-card history-card">
      <div class="app-card-head">
        <h2>运行历史</h2>
        <span class="card-head-tail">
          <span class="head-count">{{ historyTotal }} 次运行</span>
          <el-button size="small" @click="fetchHistory">刷新</el-button>
        </span>
      </div>
      <div class="app-card-body">
        <div v-if="historyError" class="history-error" role="alert">
          <span>{{ historyError.message || '历史记录加载失败' }}</span>
          <el-button size="small" type="primary" plain @click="fetchHistory">重试</el-button>
        </div>
        <el-table v-else v-loading="historyLoading" :data="history" border size="small">
          <el-table-column prop="id" label="运行 ID" width="90" />
          <el-table-column prop="goal" label="执行目标" min-width="260" show-overflow-tooltip />
          <el-table-column prop="status" label="运行状态" width="100">
            <template #default="{ row }">
              <StatusTag kind="agent" :status="row.status" size="small" />
            </template>
          </el-table-column>
          <el-table-column prop="total_steps" label="步数" width="80" />
          <el-table-column prop="final_answer" label="结果摘要" min-width="240" show-overflow-tooltip />
          <el-table-column prop="created_at" label="开始时间" width="180" />
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button size="small" type="primary" text @click="viewRun(row)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          background
          layout="total, prev, pager, next"
          :current-page="historyPage"
          :page-size="historyPageSize"
          :total="historyTotal"
          class="app-pagination-end"
          @current-change="handleHistoryPageChange"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
// StatusTag 内部渲染 el-tag，标签样式需要在用到它的组件里显式引入
import 'element-plus/es/components/tag/style/css'
import StatusTag from '../StatusTag.vue'
import { formatJson, toolLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  approvals, fetchApprovals, decideApproval,
  history, historyLoading, viewRun, historyPage, historyPageSize, historyTotal,
  fetchHistory, handleHistoryPageChange,
} = useAgentWorkbench()

/** 审批状态文案，与改造前保持一致 */
function approvalStatusLabel(status) {
  if (status === 'pending') return '待审批'
  if (status === 'approved') return '已通过'
  return '已拒绝'
}

/** 审批状态 → .app-tag 变体 */
function approvalStatusTone(status) {
  if (status === 'pending') return 'is-pending'
  if (status === 'approved') return 'is-ok'
  return 'is-danger'
}
</script>

<style scoped>
/* 两块面板之间保持方向 B 的分块间距 */
.side-panels {
  display: grid;
  gap: var(--space-6);
}

.card-head-tail {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.head-count {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.meta-row {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.7;
}

.stack-list {
  display: grid;
  gap: var(--space-3);
}

.history-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--color-danger-light);
  color: var(--color-danger);
  font-size: var(--text-sm);
}
</style>
