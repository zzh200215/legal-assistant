<template>
  <div class="app-section-intro tab-intro">
    <strong>反馈闭环与评测沉淀</strong>
    <span>统一查看正负反馈、处理状态和负反馈原因，并支持导出评测集。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <span>统计周期：</span>
      <el-radio-group v-model="feedbackDays" @change="resetFeedbackPagination">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
        <el-radio-button :value="90">近 90 天</el-radio-button>
      </el-radio-group>
      <template v-if="isAdmin">
        <span style="margin-left: 12px">范围：</span>
        <el-radio-group v-model="feedbackScope" @change="resetFeedbackPagination">
          <el-radio-button value="mine">我的</el-radio-button>
          <el-radio-button value="all">全部用户</el-radio-button>
        </el-radio-group>
      </template>
      <span style="margin-left: 12px">反馈：</span>
      <el-select v-model="feedbackValueFilter" clearable placeholder="全部" style="width: 140px" @change="resetFeedbackPagination">
        <el-option label="正反馈" value="positive" />
        <el-option label="负反馈" value="negative" />
      </el-select>
      <span>状态：</span>
      <el-select v-model="feedbackStatusFilter" clearable placeholder="全部" style="width: 140px" @change="resetFeedbackPagination">
        <el-option label="待处理" value="open" />
        <el-option label="已处理" value="resolved" />
      </el-select>
      <div class="app-toolbar-right">
        <el-button @click="fetchFeedbackData">刷新</el-button>
        <el-button v-if="isAdmin" type="warning" plain @click="exportFeedbackBundle">导出评测集</el-button>
      </div>
    </div>
  </el-card>

  <el-row :gutter="16" style="margin-bottom: 16px">
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="反馈总数" :value="feedbackStats.total_feedback || 0" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="正反馈" :value="feedbackStats.positive_count || 0" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="待处理负反馈" :value="feedbackStats.open_count || 0" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="处理率" :value="Math.round((feedbackStats.resolution_rate || 0) * 100)" suffix="%" />
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="16" style="margin-bottom: 16px">
    <el-col :span="12">
      <el-card>
        <template #header>负反馈原因</template>
        <el-table :data="feedbackReasonRows" border size="small" max-height="260">
          <el-table-column prop="label" label="原因" />
          <el-table-column prop="count" label="数量" width="100" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card>
        <template #header>每日反馈趋势</template>
        <el-table :data="feedbackDateRows" border size="small" max-height="260">
          <el-table-column prop="date" label="日期" width="140" />
          <el-table-column prop="count" label="反馈数" width="100" />
        </el-table>
      </el-card>
    </el-col>
  </el-row>

  <el-card>
    <template #header>反馈列表</template>
    <el-table :data="feedbackRows" v-loading="feedbackLoading" border size="small" max-height="560">
      <el-table-column prop="document_title" label="文档" width="180" show-overflow-tooltip />
      <el-table-column prop="question" label="问题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="feedback_value" label="反馈" width="90">
        <template #default="{ row }">
          <el-tag :type="row.feedback_value === 'positive' ? 'success' : 'danger'" size="small">
            {{ row.feedback_value === 'positive' ? '正反馈' : '负反馈' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="feedback_status" label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.feedback_status === 'resolved' ? 'success' : 'warning'" size="small">
            {{ row.feedback_status === 'resolved' ? '已处理' : '待处理' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="feedback_reason" label="原因" width="140">
        <template #default="{ row }">{{ feedbackReasonLabel(row.feedback_reason) }}</template>
      </el-table-column>
      <el-table-column prop="feedback_note" label="用户备注" show-overflow-tooltip />
      <el-table-column prop="feedback_resolution_note" label="处理备注" show-overflow-tooltip />
      <el-table-column prop="feedback_created_at" label="反馈时间" width="180" />
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="isAdmin && row.feedback_value === 'negative' && row.feedback_status === 'open'"
            text
            type="primary"
            @click="openFeedbackResolve(row)"
          >
            处理
          </el-button>
          <el-button v-else text @click="openFeedbackTarget(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      background
      layout="total, prev, pager, next"
      :current-page="feedbackPage"
      :page-size="feedbackPageSize"
      :total="feedbackTotal"
      class="app-pagination-end"
      @current-change="handleFeedbackPageChange"
    />
  </el-card>

  <el-dialog v-model="feedbackResolveVisible" title="处理反馈" width="560px" class="system-dialog" append-to-body>
    <div v-if="selectedFeedback">
      <div class="dialog-stack">
        <div><strong>文档：</strong>{{ selectedFeedback.document_title || `文档 ${selectedFeedback.document_id}` }}</div>
        <div><strong>问题：</strong>{{ selectedFeedback.question }}</div>
        <div><strong>原因：</strong>{{ feedbackReasonLabel(selectedFeedback.feedback_reason) }}</div>
        <div v-if="selectedFeedback.feedback_note"><strong>用户备注：</strong>{{ selectedFeedback.feedback_note }}</div>
        <el-input
          v-model="feedbackResolutionNote"
          type="textarea"
          :rows="3"
          :maxlength="300"
          show-word-limit
          placeholder="填写处理结论"
        />
      </div>
    </div>
    <template #footer>
      <el-button @click="feedbackResolveVisible = false">取消</el-button>
      <el-button type="primary" :loading="resolvingFeedback" @click="submitFeedbackResolve">确认处理</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemFeedback } from '../../composables/useSystemFeedback'
import { useAuthStore } from '../../stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  feedbackDays, feedbackScope, feedbackValueFilter, feedbackStatusFilter, feedbackRows, feedbackStats,
  feedbackLoading, feedbackPage, feedbackPageSize, feedbackTotal, feedbackResolveVisible,
  selectedFeedback, feedbackResolutionNote, resolvingFeedback, fetchFeedbackData, exportFeedbackBundle,
  resetFeedbackPagination, handleFeedbackPageChange, openFeedbackTarget, openFeedbackResolve,
  submitFeedbackResolve,
} = useSystemFeedback({ client: api, message: ElMessage, router, isAdmin })

const feedbackReasonRows = computed(() => {
  const byReason = feedbackStats.value.by_reason || {}
  return Object.entries(byReason)
    .map(([key, count]) => ({ key, label: feedbackReasonLabel(key), count }))
    .sort((a, b) => b.count - a.count)
})

const feedbackDateRows = computed(() => {
  const byDate = feedbackStats.value.by_date || {}
  return Object.entries(byDate)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))
})

const feedbackReasonLabel = (reason) => ({
  incorrect_answer: '答案不准确',
  wrong_citation: '引用不准确',
  incomplete_answer: '信息不完整',
  not_helpful: '没有帮助',
}[reason] || reason || '未分类')

onMounted(async () => {
  await authStore.loadMe()
  fetchFeedbackData()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.dialog-stack {
  display: grid;
  gap: var(--space-2);
}
.system-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
</style>
