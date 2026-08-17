<template>
  <div class="app-section-intro tab-intro">
    <strong>成本与使用分析</strong>
    <span>跟踪调用量、限额、成本和问答回放，判断模型资源是否被有效使用。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <span>统计周期：</span>
      <el-radio-group v-model="tokenDays" @change="resetTokenPagination">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
        <el-radio-button :value="90">近 90 天</el-radio-button>
      </el-radio-group>
      <div class="app-toolbar-right">
        <el-button @click="fetchTokenData">刷新</el-button>
      </div>
    </div>
  </el-card>

  <el-row :gutter="16" class="system-block-row">
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="总调用次数" :value="myStats.total_calls" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="总 Token 数" :value="myStats.total_tokens" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="输入 Token" :value="myStats.total_prompt_tokens" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="输出 Token" :value="myStats.total_completion_tokens" />
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="16" class="system-block-row">
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="今日已用请求" :value="myGovernance.today.used_requests || 0" />
        <div style="margin-top: 8px; color: var(--color-text-muted)">
          剩余：{{ myGovernance.today.remaining_requests ?? '不限' }}
        </div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="今日已用 Token" :value="myGovernance.today.used_tokens || 0" />
        <div style="margin-top: 8px; color: var(--color-text-muted)">
          剩余：{{ myGovernance.today.remaining_tokens ?? '不限' }}
        </div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="当前窗口请求数" :value="myGovernance.rate_limit.current_requests || 0" />
        <div style="margin-top: 8px; color: var(--color-text-muted)">
          限额：{{ myGovernance.rate_limit.max_requests || '未启用' }} / {{ myGovernance.rate_limit.window_seconds || 0 }}s
        </div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="今日拦截次数" :value="myGovernance.today.blocked_requests || 0" />
        <div style="margin-top: 8px; color: var(--color-text-muted)">
          预留输出：{{ myGovernance.policy.estimated_completion_tokens || 0 }} Token
        </div>
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="16" class="system-block-row">
    <el-col :span="12">
      <el-card>
        <template #header>按功能分类</template>
        <el-table :data="actionRows" border size="small">
          <el-table-column prop="action" label="功能" />
          <el-table-column prop="calls" label="调用次数" width="100" />
          <el-table-column prop="total_tokens" label="Token 数" width="120" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card>
        <template #header>每日使用趋势</template>
        <el-table :data="dateRows" border size="small" max-height="300">
          <el-table-column prop="date" label="日期" width="120" />
          <el-table-column prop="calls" label="调用次数" width="100" />
          <el-table-column prop="total_tokens" label="Token 数" width="120" />
        </el-table>
      </el-card>
    </el-col>
  </el-row>

  <el-card v-if="isAdmin && globalStats.total_calls" class="system-panel-card">
    <template #header>全局统计</template>
    <el-descriptions :column="4" border>
      <el-descriptions-item label="总调用次数">{{ globalStats.total_calls }}</el-descriptions-item>
      <el-descriptions-item label="总 Token 数">{{ globalStats.total_tokens }}</el-descriptions-item>
      <el-descriptions-item label="输入 Token">{{ globalStats.total_prompt_tokens }}</el-descriptions-item>
      <el-descriptions-item label="输出 Token">{{ globalStats.total_completion_tokens }}</el-descriptions-item>
      <el-descriptions-item label="今日请求数">{{ globalGovernance.today.total_requests || 0 }}</el-descriptions-item>
      <el-descriptions-item label="今日 Token">{{ globalGovernance.today.total_tokens || 0 }}</el-descriptions-item>
      <el-descriptions-item label="今日拦截">{{ globalGovernance.today.blocked_requests || 0 }}</el-descriptions-item>
      <el-descriptions-item label="分钟限流">{{ globalGovernance.policy.rate_limit_max_requests || '未启用' }} / {{ globalGovernance.policy.rate_limit_window_seconds || 0 }}s</el-descriptions-item>
    </el-descriptions>
    <div v-if="Object.keys(globalStats.by_model || {}).length" style="margin-top: 12px">
      <strong>按模型：</strong>
      <el-tag v-for="(value, model) in globalStats.by_model" :key="model" style="margin: 4px">
        {{ model }}: {{ value.calls }} 次 / {{ value.total_tokens }} Token
      </el-tag>
    </div>
  </el-card>

  <el-card class="system-panel-card">
    <template #header>模型调用概览</template>
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="6">
        <el-statistic title="调用次数" :value="llmStats.total_calls || 0" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="失败次数" :value="llmStats.failed_calls || 0" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="平均耗时(ms)" :value="llmStats.avg_duration_ms || 0" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="成功率" :value="Math.round((llmStats.success_rate || 0) * 100)" suffix="%" />
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>按模块统计</template>
          <el-table :data="llmModuleRows" size="small" border max-height="220">
            <el-table-column prop="module_name" label="模块" />
            <el-table-column prop="count" label="调用数" width="110" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>按动作统计</template>
          <el-table :data="llmActionRows" size="small" border max-height="220">
            <el-table-column prop="action" label="动作" />
            <el-table-column prop="calls" label="调用数" width="90" />
            <el-table-column prop="failed" label="失败数" width="90" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>模型调用失败趋势</template>
          <el-table :data="llmFailedDateRows" size="small" border max-height="220">
            <el-table-column prop="date" label="日期" width="140" />
            <el-table-column prop="count" label="失败数" width="100" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>Agent 执行成功率</template>
          <el-row :gutter="12">
            <el-col :span="8">
              <el-statistic title="总执行数" :value="agentRunCount" />
            </el-col>
            <el-col :span="8">
              <el-statistic title="成功数" :value="agentSucceededCount" />
            </el-col>
            <el-col :span="8">
              <el-statistic title="成功率" :value="Math.round(agentSuccessRate * 100)" suffix="%" />
            </el-col>
          </el-row>
        </el-card>
      </el-col>
    </el-row>

    <el-table :data="llmCalls" v-loading="llmLoading" border size="small" max-height="360">
      <el-table-column prop="module_name" label="模块" width="100">
        <template #default="{ row }">{{ moduleLabel(row.module_name) }}</template>
      </el-table-column>
      <el-table-column prop="action" label="动作" width="180" show-overflow-tooltip />
      <el-table-column prop="model_name" label="模型" width="120" />
      <el-table-column prop="prompt_template" label="Prompt" width="160" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
            {{ row.status === 'success' ? '成功' : '失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="duration_ms" label="耗时(ms)" width="100" />
      <el-table-column prop="input_tokens" label="输入" width="80" />
      <el-table-column prop="output_tokens" label="输出" width="80" />
      <el-table-column prop="error_message" label="错误信息" show-overflow-tooltip />
      <el-table-column prop="created_at" label="时间" width="180" />
    </el-table>
    <el-pagination
      background
      layout="total, prev, pager, next"
      :current-page="llmPage"
      :page-size="llmPageSize"
      :total="llmTotal"
      class="app-pagination-end"
      @current-change="handleLlmPageChange"
    />
  </el-card>

  <el-row :gutter="16" class="system-block-row">
    <el-col :span="12">
      <el-card>
        <template #header>成本统计</template>
        <el-row :gutter="12" style="margin-bottom: 12px">
          <el-col :span="8">
            <el-statistic title="计费调用数" :value="billingSummary.metered_calls || 0" />
          </el-col>
          <el-col :span="8">
            <el-statistic title="未映射模型" :value="billingSummary.unpriced_calls || 0" />
          </el-col>
          <el-col :span="8">
            <el-statistic :title="`总成本(${billingCurrency})`" :value="billingSummary.total_cost || 0" />
          </el-col>
        </el-row>
        <el-table :data="billingModelRows" border size="small" max-height="280">
          <el-table-column prop="model_name" label="模型" min-width="140" />
          <el-table-column prop="calls" label="调用数" width="90" />
          <el-table-column prop="priced_calls" label="计费数" width="90" />
          <el-table-column prop="input_tokens" label="输入 Token" width="110" />
          <el-table-column prop="output_tokens" label="输出 Token" width="110" />
          <el-table-column :label="`成本(${billingCurrency})`" prop="total_cost" width="120" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card>
        <template #header>模型价格表</template>
        <el-table :data="pricingRows" border size="small" max-height="280">
          <el-table-column prop="model_name" label="模型" min-width="140" />
          <el-table-column :label="`输入 / 1K(${billingCurrency})`" prop="input_per_1k" width="130" />
          <el-table-column :label="`输出 / 1K(${billingCurrency})`" prop="output_per_1k" width="130" />
        </el-table>
        <div v-if="billingSummary.unmapped_models?.length" style="margin-top: 12px; color: var(--color-warning)">
          未映射模型：{{ billingSummary.unmapped_models.join('、') }}
        </div>
      </el-card>
    </el-col>
  </el-row>

  <el-card class="system-panel-card">
    <template #header>问答回放</template>
    <el-table :data="qaReplayRows" border size="small" max-height="360">
      <el-table-column prop="document_title" label="文档" min-width="140" show-overflow-tooltip />
      <el-table-column prop="question" label="问题" min-width="180" show-overflow-tooltip />
      <el-table-column prop="source" label="来源" width="100">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ qaSourceLabel(row.source) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="model_name" label="模型" width="120" />
      <el-table-column prop="latency_ms" label="耗时(ms)" width="100" />
      <el-table-column prop="feedback_status" label="反馈状态" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.feedback_status === 'open'" size="small" type="warning">待处理</el-tag>
          <el-tag v-else-if="row.feedback_status === 'resolved'" size="small" type="success">已处理</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="时间" width="180" />
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button text type="primary" @click="openQaReplay(row)">回放</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="qaReplayVisible" title="问答回放" width="760px" class="system-dialog" append-to-body>
    <template v-if="selectedQaReplay">
      <div class="dialog-stack">
        <div><strong>文档：</strong>{{ selectedQaReplay.document_title || `#${selectedQaReplay.document_id}` }}</div>
        <div><strong>问题：</strong>{{ selectedQaReplay.question }}</div>
        <div><strong>回答：</strong><div class="dialog-pre" style="margin-top: 6px">{{ selectedQaReplay.answer }}</div></div>
        <div><strong>命中引用：</strong></div>
        <el-empty v-if="!selectedQaReplay.citations?.length" description="暂无引用" />
        <el-card v-for="(item, index) in (selectedQaReplay.citations || [])" :key="`qa-replay-citation-${index}`" shadow="never" class="citation-card">
          <div class="dialog-stack">
            <div>
              <el-tag size="small" type="primary">片段 {{ (item.chunk_index ?? index) + 1 }}</el-tag>
              <span style="margin-left: 8px">{{ item.section_title || '未标注章节' }}</span>
              <span v-if="item.page_number" style="margin-left: 8px; color: var(--color-text-muted)">第 {{ item.page_number }} 页</span>
            </div>
            <blockquote class="citation-quote">{{ item.source_text || '暂无原文片段' }}</blockquote>
          </div>
        </el-card>
      </div>
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
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemTokenAnalytics } from '../../composables/useSystemTokenAnalytics'
import { useSystemTaskMonitor } from '../../composables/useSystemTaskMonitor'
import { useAuthStore } from '../../stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  tokenDays, myStats, globalStats, llmPage, llmPageSize, llmTotal, llmStats, llmCalls,
  llmLoading, billingStats, qaReplayRows, qaReplayVisible, selectedQaReplay, fetchTokenData,
  openQaReplay, resetTokenPagination, handleLlmPageChange,
} = useSystemTokenAnalytics({ client: api, message: ElMessage, isAdmin })

const { agentRunCount, agentSucceededCount, agentSuccessRate } = useSystemTaskMonitor({ client: api, message: ElMessage, router, isAdmin })

const emptyGovernance = () => ({ today: {}, rate_limit: {}, policy: {} })
const myGovernance = computed(() => ({ ...emptyGovernance(), ...(myStats.value.governance || {}) }))
const globalGovernance = computed(() => ({ today: {}, policy: {}, ...(globalStats.value.governance || {}) }))

const actionRows = computed(() => {
  const byAction = myStats.value.by_action || {}
  const labels = {
    chat: '聊天',
    chat_stream: '聊天流式',
    rag_answer: '文档问答',
    rag_chat_stream: '文档问答流式',
    document_summary: '文档摘要',
    document_risk_extract: '文档风险提取',
    document_todo_extract: '文档待办提取',
    document_clause_extract: '文档条款提取',
    document_compare: '文档对比',
    legal_consultation: '法律咨询',
    agent_plan: 'Agent 规划',
    task_extract_from_chat: '聊天待办提取',
    task_decompose: '任务拆解',
  }
  return Object.entries(byAction)
    .map(([action, value]) => ({
      action: labels[action] || action,
      action_key: action,
      calls: value.calls,
      total_tokens: value.total_tokens,
    }))
    .sort((a, b) => b.total_tokens - a.total_tokens)
})

const dateRows = computed(() => {
  const byDate = myStats.value.by_date || {}
  return Object.entries(byDate)
    .map(([date, value]) => ({ date, calls: value.calls, total_tokens: value.total_tokens }))
    .sort((a, b) => a.date.localeCompare(b.date))
})

const llmModuleRows = computed(() => {
  const byModule = llmStats.value.by_module || {}
  return Object.entries(byModule)
    .map(([module_name, count]) => ({ module_name, count }))
    .sort((a, b) => b.count - a.count)
})

const llmActionRows = computed(() => {
  const byAction = llmStats.value.by_action || {}
  return Object.entries(byAction)
    .map(([action, value]) => ({ action, calls: value.calls, failed: value.failed }))
    .sort((a, b) => b.calls - a.calls)
})

const llmFailedDateRows = computed(() => {
  const byDate = llmStats.value.failed_by_date || {}
  return Object.entries(byDate)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))
})

const billingSummary = computed(() => billingStats.value.summary || {})
const billingCurrency = computed(() => billingStats.value.summary?.currency || billingStats.value.pricing?.currency || 'CNY')
const pricingRows = computed(() => billingStats.value.pricing?.items || [])
const billingModelRows = computed(() => {
  const byModel = billingStats.value.by_model || {}
  return Object.entries(byModel)
    .map(([model_name, value]) => ({
      model_name,
      calls: value.calls || 0,
      priced_calls: value.priced_calls || 0,
      input_tokens: value.input_tokens || 0,
      output_tokens: value.output_tokens || 0,
      total_cost: value.total_cost || 0,
    }))
    .sort((a, b) => b.total_cost - a.total_cost)
})

const moduleLabel = (mod) => ({
  document: '文档',
  legal: '法律文书',
  task: '任务',
  agent: 'Agent',
  async_task: '异步任务',
  system: '系统',
  chat: '聊天',
  prompt: '提示词',
  auth: '认证',
}[mod] || mod)

const qaSourceLabel = (source) => ({
  document: '文档问答',
  chat: '聊天问答',
  ws_chat: '流式问答',
}[source] || source || '未知')

onMounted(async () => {
  await authStore.loadMe()
  fetchTokenData()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.system-block-row {
  margin-top: var(--space-4);
  margin-bottom: 0;
}
.dialog-stack {
  display: grid;
  gap: var(--space-2);
}
.dialog-pre {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--color-text);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
}
.citation-quote {
  margin: 0;
  color: var(--color-text);
}
.citation-card {
  border-radius: var(--radius-md);
}
.system-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
</style>
