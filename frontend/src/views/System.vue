<template>
  <div class="system-page">
    <div class="page-heading">
      <div>
        <h3>系统中心</h3>
        <p>统一查看平台健康、成本使用、连接器同步、反馈闭环和任务运行状态。</p>
      </div>
    </div>

    <div class="system-overview">
      <div class="overview-tile">
        <span>运行任务</span>
        <strong>{{ runningTaskCount }}</strong>
      </div>
      <div class="overview-tile">
        <span>失败任务</span>
        <strong>{{ failedTaskCount }}</strong>
      </div>
      <div class="overview-tile">
        <span>待重试</span>
        <strong>{{ retryableTaskCount }}</strong>
      </div>
      <div class="overview-tile">
        <span>Agent 成功率</span>
        <strong>{{ Math.round(agentSuccessRate * 100) }}%</strong>
      </div>
    </div>

    <div class="system-command-bar">
      <div class="command-copy">
        <div class="section-eyebrow">平台控制</div>
        <strong>运维与质量控制台</strong>
        <span>集中处理健康检查、成本、反馈、审批、连接器和后台任务。</span>
      </div>
      <div class="command-chips">
        <span class="command-chip">当前标签：{{ activeTab }}</span>
        <span class="command-chip">失败任务：{{ failedTaskCount }}</span>
        <span class="command-chip">待审批：{{ approvalStats.pending || 0 }}</span>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="system-tabs" @tab-change="syncTabQuery">
      <el-tab-pane label="健康检查" name="health">
        <div class="app-section-intro tab-intro">
          <strong>平台基础健康</strong>
          <span>查看核心服务可用性、依赖提供方状态和最近一次健康检查时间。</span>
        </div>

        <el-card class="system-panel-card">
          <div class="app-toolbar">
            <div>
              <strong>系统状态：</strong>
              <el-tag :type="healthStatusTagType(healthData.status)" style="margin-left: 8px">
                {{ healthStatusLabel(healthData.status) }}
              </el-tag>
              <span v-if="healthData.timestamp" style="margin-left: 12px; color: var(--color-text-muted)">
                {{ healthData.timestamp }}
              </span>
            </div>
            <el-button :loading="healthLoading" @click="fetchHealthData">刷新</el-button>
          </div>
        </el-card>

        <el-row :gutter="16" class="system-block-row">
          <el-col :span="8" v-for="(check, name) in (healthData.checks || {})" :key="name">
            <el-card shadow="hover">
              <template #header>
                <div style="display: flex; justify-content: space-between; align-items: center">
                  <span>{{ healthCheckLabel(name) }}</span>
                  <el-tag :type="check.status === 'ok' ? 'success' : 'danger'" size="small">
                    {{ check.status === 'ok' ? '正常' : '异常' }}
                  </el-tag>
                </div>
              </template>
              <div style="display: grid; gap: 8px; color: var(--color-text-secondary)">
                <div v-if="check.provider"><strong>服务提供方：</strong> {{ check.provider }}</div>
                <div v-if="check.message" style="color: var(--color-danger); white-space: pre-wrap">{{ check.message }}</div>
                <div v-if="!check.message" style="color: var(--color-success)">连接正常</div>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <el-tab-pane label="用户漏斗" name="funnel">
        <PilotAnalyticsTabs section="funnel" />
      </el-tab-pane>

      <el-tab-pane label="留存与北极星" name="retention">
        <PilotAnalyticsTabs section="retention" />
      </el-tab-pane>

      <el-tab-pane label="Token 统计" name="tokens">
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
      </el-tab-pane>

      <el-tab-pane label="操作日志" name="oplogs">
        <div class="app-section-intro tab-intro">
          <strong>操作回放</strong>
          <span>按模块、范围和时间回看系统行为，定位用户操作和平台侧事件。</span>
        </div>

        <el-card class="system-panel-card">
          <div class="app-toolbar">
            <span>统计周期：</span>
            <el-radio-group v-model="logDays" @change="resetLogPagination">
              <el-radio-button :value="7">近 7 天</el-radio-button>
              <el-radio-button :value="30">近 30 天</el-radio-button>
              <el-radio-button :value="90">近 90 天</el-radio-button>
            </el-radio-group>
            <template v-if="isAdmin">
              <span style="margin-left: 12px">范围：</span>
              <el-radio-group v-model="logScope" @change="resetLogPagination">
                <el-radio-button value="mine">我的</el-radio-button>
                <el-radio-button value="all">全部用户</el-radio-button>
              </el-radio-group>
            </template>
            <span style="margin-left: 12px">模块筛选：</span>
            <el-select v-model="logModule" clearable placeholder="全部" style="width: 140px" @change="resetLogPagination">
              <el-option label="文档" value="document" />
              <el-option label="会议" value="meeting" />
              <el-option label="法律文书" value="legal" />
              <el-option label="任务" value="task" />
              <el-option label="Agent" value="agent" />
              <el-option label="异步任务" value="async_task" />
              <el-option v-if="isAdmin" label="系统" value="system" />
              <el-option label="聊天" value="chat" />
              <el-option label="提示词" value="prompt" />
              <el-option label="认证" value="auth" />
            </el-select>
            <div class="app-toolbar-right">
              <el-button @click="fetchLogData">刷新</el-button>
            </div>
          </div>
        </el-card>

        <el-card v-if="logStats.total_operations" class="system-panel-card">
          <template #header>操作统计</template>
          <el-descriptions :column="4" border>
            <el-descriptions-item label="总操作数">{{ logStats.total_operations }}</el-descriptions-item>
            <el-descriptions-item v-for="(count, mod) in logStats.by_module" :key="mod" :label="moduleLabel(mod)">
              {{ count }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card class="system-panel-card">
          <template #header>操作记录</template>
          <el-table :data="logs" v-loading="logLoading" border size="small" max-height="500">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="module" label="模块" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="moduleTagType(row.module)">{{ moduleLabel(row.module) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="action" label="操作" show-overflow-tooltip />
            <el-table-column prop="target_type" label="目标类型" width="100" />
            <el-table-column prop="target_id" label="目标 ID" width="80" />
            <el-table-column prop="detail" label="详情" show-overflow-tooltip />
            <el-table-column prop="ip_address" label="IP" width="130" />
            <el-table-column prop="created_at" label="时间" width="170" />
          </el-table>
          <el-pagination
            background
            layout="total, prev, pager, next"
            :current-page="logPage"
            :page-size="logPageSize"
            :total="logTotal"
            class="app-pagination-end"
            @current-change="handleLogPageChange"
          />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="异常告警" name="alerts">
        <div class="app-section-intro tab-intro">
          <strong>异常与风险告警</strong>
          <span>聚合审查、法源与外发风险，按来源、分类和级别快速筛查。</span>
        </div>

        <el-card class="system-panel-card">
          <div class="app-toolbar">
            <span>统计周期：</span>
            <el-radio-group v-model="alertDays" @change="resetAlertPagination">
              <el-radio-button :value="7">近 7 天</el-radio-button>
              <el-radio-button :value="30">近 30 天</el-radio-button>
              <el-radio-button :value="90">近 90 天</el-radio-button>
            </el-radio-group>
            <template v-if="isAdmin">
              <span style="margin-left: 12px">范围：</span>
              <el-radio-group v-model="alertScope" @change="resetAlertPagination">
                <el-radio-button value="mine">我的</el-radio-button>
                <el-radio-button value="all">全部用户</el-radio-button>
              </el-radio-group>
            </template>
            <span style="margin-left: 12px">来源：</span>
            <el-select v-model="alertSource" clearable placeholder="全部" style="width: 140px" @change="resetAlertPagination">
              <el-option label="异步任务" value="async_task" />
              <el-option label="Agent" value="agent" />
              <el-option label="计划任务" value="scheduler" />
              <el-option label="法源同步" value="mailbox" />
              <el-option label="外发文书" value="outbound_email" />
            </el-select>
            <span>分类：</span>
            <el-select v-model="alertCategory" clearable placeholder="全部" style="width: 180px" @change="resetAlertPagination">
              <el-option v-for="item in alertCategoryOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <span>级别：</span>
            <el-select v-model="alertSeverity" clearable placeholder="全部" style="width: 140px" @change="resetAlertPagination">
              <el-option label="高" value="high" />
              <el-option label="中" value="medium" />
              <el-option label="低" value="low" />
            </el-select>
            <div class="app-toolbar-right">
              <el-button @click="fetchAlerts">刷新</el-button>
            </div>
          </div>
        </el-card>

        <el-row :gutter="16" class="system-block-row">
          <el-col :span="6">
            <el-card shadow="hover">
              <el-statistic title="告警总数" :value="alertStats.total || 0" />
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover">
              <el-statistic title="计划任务风险" :value="alertStats.by_source?.scheduler || 0" />
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover">
              <el-statistic title="法源同步失败" :value="alertStats.by_source?.mailbox || 0" />
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover">
              <el-statistic title="外发文书风险" :value="alertStats.by_source?.outbound_email || 0" />
            </el-card>
          </el-col>
        </el-row>

        <el-row :gutter="16" class="system-block-row">
          <el-col :span="12">
            <el-card>
              <template #header>分类分布</template>
              <el-table :data="alertCategoryRows" border size="small" max-height="260">
                <el-table-column prop="label" label="分类" />
                <el-table-column prop="count" label="数量" width="100" />
              </el-table>
            </el-card>
          </el-col>
          <el-col :span="12">
            <el-card>
              <template #header>每日告警趋势</template>
              <el-table :data="alertDateRows" border size="small" max-height="260">
                <el-table-column prop="date" label="日期" width="140" />
                <el-table-column prop="count" label="告警数" width="100" />
              </el-table>
            </el-card>
          </el-col>
        </el-row>

        <el-card class="system-panel-card">
          <template #header>告警列表</template>
          <el-table :data="alerts" v-loading="alertLoading" border size="small" max-height="520">
            <el-table-column prop="source" label="来源" width="110">
              <template #default="{ row }">
                <el-tag :type="alertSourceTagType(row.source)" size="small">
                  {{ row.source_label || row.source }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="severity" label="级别" width="90">
              <template #default="{ row }">
                <el-tag :type="alertSeverityTagType(row.severity)" size="small">
                  {{ alertSeverityLabel(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="分类" width="150">
              <template #default="{ row }">
                {{ alertCategoryLabel(row.category) }}
              </template>
            </el-table-column>
            <el-table-column prop="title" label="事件" width="220" show-overflow-tooltip />
            <el-table-column prop="error_type" label="错误类型" width="160" show-overflow-tooltip />
            <el-table-column prop="target_type" label="目标类型" width="100" />
            <el-table-column prop="target_id" label="目标 ID" width="90" />
            <el-table-column prop="message" label="详情" show-overflow-tooltip />
            <el-table-column prop="created_at" label="时间" width="180" />
          </el-table>
          <el-pagination
            background
            layout="total, prev, pager, next"
            :current-page="alertPage"
            :page-size="alertPageSize"
            :total="alertTotal"
            class="app-pagination-end"
            @current-change="handleAlertPageChange"
          />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="实验观测" name="experiments">
        <template v-if="!isAdmin">
          <el-card class="system-panel-card">
            <div class="app-readonly-banner">
              <strong>仅管理员可见</strong>
              <span>实验观测面板包含 Prompt 灰度与评测数据，当前账号无权限查看。</span>
            </div>
          </el-card>
        </template>
        <template v-else>
          <div class="app-section-intro tab-intro">
            <strong>实验与灰度观测</strong>
            <span>评估 Prompt 版本效果、流量覆盖情况和基线回退风险。</span>
          </div>

          <el-card class="system-panel-card">
            <div class="app-toolbar">
              <span>流量窗口：</span>
              <el-radio-group v-model="experimentDays" @change="fetchExperimentOverview">
                <el-radio-button :value="7">近 7 天</el-radio-button>
                <el-radio-button :value="30">近 30 天</el-radio-button>
                <el-radio-button :value="90">近 90 天</el-radio-button>
              </el-radio-group>
              <div class="app-toolbar-right">
                <el-button :loading="experimentLoading" @click="fetchExperimentOverview">刷新</el-button>
              </div>
            </div>
          </el-card>

          <el-row :gutter="16" style="margin-bottom: 16px">
            <el-col :span="6">
              <el-card shadow="hover">
                <el-statistic title="评测样本数" :value="experimentSummary.dataset_size || 0" />
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="hover">
                <el-statistic title="实验数量" :value="experimentSummary.experiment_count || 0" />
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="hover">
                <el-statistic title="回退风险实验" :value="experimentSummary.degraded_experiment_count || 0" />
              </el-card>
            </el-col>
            <el-col :span="6">
              <el-card shadow="hover">
                <el-statistic title="灰度中的 Prompt" :value="rolloutSummary.active_rollout_count || 0" />
              </el-card>
            </el-col>
          </el-row>

          <el-card style="margin-bottom: 16px">
            <template #header>评测产物状态</template>
            <el-descriptions :column="4" border>
              <el-descriptions-item label="输出目录">{{ experimentArtifacts.output_dir || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Baseline">{{ experimentSummary.baseline_experiment || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Bundle">{{ experimentSummary.bundle_meta?.bundle_name || '默认样例集' }}</el-descriptions-item>
              <el-descriptions-item label="Summary 产物">{{ experimentArtifacts.summary?.exists ? '已生成' : '未生成' }}</el-descriptions-item>
              <el-descriptions-item label="Summary 更新时间">{{ experimentArtifacts.summary?.updated_at || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Baseline 快照">{{ experimentArtifacts.baseline_snapshot?.exists ? '已生成' : '未生成' }}</el-descriptions-item>
              <el-descriptions-item label="快照更新时间">{{ experimentArtifacts.baseline_snapshot?.updated_at || '-' }}</el-descriptions-item>
              <el-descriptions-item label="当前基线 Citation">{{ formatRate(experimentSummary.baseline_snapshot?.summary?.citation_accuracy) }}</el-descriptions-item>
            </el-descriptions>
          </el-card>

          <el-row :gutter="16" style="margin-bottom: 16px">
            <el-col :span="15">
              <el-card>
                <template #header>实验结果</template>
                <el-table :data="experimentRows" v-loading="experimentLoading" border size="small" max-height="420">
                  <el-table-column prop="name" label="实验" width="150" show-overflow-tooltip />
                  <el-table-column prop="prompt_label" label="Prompt" width="140" show-overflow-tooltip />
                  <el-table-column prop="top_k" label="TopK" width="70" />
                  <el-table-column prop="citation_accuracy_text" label="Citation" width="100" />
                  <el-table-column prop="hit_at_k_text" label="Hit@K" width="90" />
                  <el-table-column prop="refusal_accuracy_text" label="Refusal" width="90" />
                  <el-table-column prop="badcase_count" label="Badcase" width="90" />
                  <el-table-column label="对基线">
                    <template #default="{ row }">
                      <el-tag v-if="row.regression_metrics?.length" type="danger" size="small">
                        {{ row.regression_metrics.join(', ') }}
                      </el-tag>
                      <span v-else style="color: var(--color-success)">无回退</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="配置漂移" width="90">
                    <template #default="{ row }">{{ row.config_drift_count }}</template>
                  </el-table-column>
                </el-table>
              </el-card>
            </el-col>
            <el-col :span="9">
              <el-card>
                <template #header>灰度发布</template>
                <el-table :data="rolloutRows" v-loading="experimentLoading" border size="small" max-height="420">
                  <el-table-column prop="name" label="模板" min-width="150" show-overflow-tooltip />
                  <el-table-column prop="stable_version" label="稳定版" width="80" />
                  <el-table-column prop="rollout_version" label="灰度版" width="80" />
                  <el-table-column prop="percentage_text" label="比例" width="80" />
                  <el-table-column prop="started_at" label="开始时间" width="180" />
                </el-table>
              </el-card>
            </el-col>
          </el-row>

          <el-card>
            <template #header>Prompt 实际流量覆盖</template>
            <el-table :data="promptTrafficRows" v-loading="experimentLoading" border size="small" max-height="420">
              <el-table-column prop="prompt_template" label="Prompt" min-width="160" show-overflow-tooltip />
              <el-table-column prop="prompt_version" label="版本" width="80">
                <template #default="{ row }">v{{ row.prompt_version }}</template>
              </el-table-column>
              <el-table-column prop="calls" label="调用数" width="90" />
              <el-table-column prop="failed_calls" label="失败数" width="90" />
              <el-table-column prop="success_rate_text" label="成功率" width="90" />
              <el-table-column prop="last_called_at" label="最近命中" width="180" />
            </el-table>
          </el-card>
        </template>
      </el-tab-pane>

      <el-tab-pane label="反馈闭环" name="feedback">
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
      </el-tab-pane>

      <el-tab-pane label="工具健康" name="tool_health">
        <div class="app-section-intro tab-intro">
          <strong>工具健康概览</strong>
          <span>跟踪 MCP 工具调用量、成功率、审批挂起次数和最近错误。</span>
        </div>

        <el-card class="system-panel-card">
          <div class="app-toolbar">
            <div class="app-empty-note">查看 MCP 工具最近调用量、成功率、审批挂起次数与最近错误。</div>
            <el-button :loading="toolHealthLoading" @click="fetchToolHealth">刷新</el-button>
          </div>
        </el-card>

        <el-card class="system-panel-card">
          <template #header>工具健康概览</template>
          <el-table :data="toolHealthRows" v-loading="toolHealthLoading" border size="small" max-height="560">
            <el-table-column prop="tool_name" label="工具" width="180" />
            <el-table-column prop="calls" label="调用数" width="90" />
            <el-table-column prop="success_rate" label="成功率" width="100">
              <template #default="{ row }">{{ formatRate(row.success_rate) }}</template>
            </el-table-column>
            <el-table-column prop="failed_calls" label="失败数" width="90" />
            <el-table-column prop="pending_approval_calls" label="审批挂起" width="100" />
            <el-table-column prop="avg_duration_ms" label="平均耗时(ms)" width="120" />
            <el-table-column prop="last_error" label="最近错误" show-overflow-tooltip />
            <el-table-column prop="last_called_at" label="最近调用" width="180" />
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="审批台" name="approvals">
        <div class="app-section-intro tab-intro">
          <strong>人工审批中心</strong>
          <span>查看 Agent 高风险工具的审批状态，并快速跳转到执行台完成处理。</span>
        </div>

        <el-card class="system-panel-card">
          <div class="app-toolbar">
            <div class="app-empty-note">查看 Agent 高风险工具审批状态，并进入执行台处理待审批请求。</div>
            <el-button :loading="approvalsLoading" @click="fetchApprovalData">刷新</el-button>
          </div>
        </el-card>

        <el-row :gutter="16" style="margin-bottom: 16px">
          <el-col :span="8">
            <el-card shadow="hover">
              <el-statistic title="待审批" :value="approvals.filter((item) => item.status === 'pending').length" />
            </el-card>
          </el-col>
          <el-col :span="8">
            <el-card shadow="hover">
              <el-statistic title="已通过" :value="approvals.filter((item) => item.status === 'approved').length" />
            </el-card>
          </el-col>
          <el-col :span="8">
            <el-card shadow="hover">
              <el-statistic title="已执行" :value="approvals.filter((item) => item.status === 'executed').length" />
            </el-card>
          </el-col>
        </el-row>

        <el-card class="system-panel-card">
          <template #header>审批请求</template>
          <el-table :data="approvals" v-loading="approvalsLoading" border size="small" max-height="520">
            <el-table-column prop="tool_name" label="工具" width="180" />
            <el-table-column prop="agent_type" label="Agent" width="140" />
            <el-table-column prop="risk_level" label="风险级别" width="100">
              <template #default="{ row }">
                <el-tag :type="row.risk_level === 'high' ? 'danger' : 'warning'" size="small">{{ row.risk_level }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="110">
              <template #default="{ row }">
                <el-tag
                  size="small"
                  :type="row.status === 'pending' ? 'warning' : row.status === 'approved' ? 'success' : row.status === 'executed' ? 'primary' : 'info'"
                >
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="input_params" label="参数" show-overflow-tooltip />
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status === 'pending'" text type="primary" @click="openApprovalInAgent(row)">去处理</el-button>
                <span v-else>-</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="知识库" name="knowledge">
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
      </el-tab-pane>

      <el-tab-pane label="组织架构" name="orgs">
        <template v-if="!isAdmin">
          <el-card class="system-panel-card">
            <div class="app-readonly-banner">
              <strong>仅管理员可管理组织架构</strong>
              <span>当前账号只能查看组织归属信息，不能创建组织、部门或修改用户归属。</span>
            </div>
          </el-card>
        </template>
        <template v-else>
          <div class="app-section-intro tab-intro">
            <strong>组织、部门与归属管理</strong>
            <span>维护组织结构、部门清单和用户归属，为权限与共享范围提供基础数据。</span>
          </div>

          <el-row :gutter="16" class="system-block-row">
            <el-col :span="12">
              <el-card>
                <template #header>创建组织</template>
                <div style="display: grid; gap: 12px">
                  <el-input v-model="newOrg.name" placeholder="组织名称" />
                  <el-input v-model="newOrg.code" placeholder="组织编码" />
                  <el-input v-model="newOrg.description" placeholder="组织说明" />
                  <el-button type="primary" :loading="orgLoading" @click="createOrganization">创建组织</el-button>
                </div>
              </el-card>
            </el-col>
            <el-col :span="12">
              <el-card>
                <template #header>创建部门</template>
                <div style="display: grid; gap: 12px">
                  <el-select v-model="newDepartment.organization_id" placeholder="选择组织">
                    <el-option v-for="item in organizations" :key="item.id" :label="item.name" :value="item.id" />
                  </el-select>
                  <el-input v-model="newDepartment.name" placeholder="部门名称" />
                  <el-input v-model="newDepartment.code" placeholder="部门编码" />
                  <el-input v-model="newDepartment.description" placeholder="部门说明" />
                  <el-button type="primary" :loading="orgLoading" @click="createDepartment">创建部门</el-button>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <el-row :gutter="16" class="system-block-row">
            <el-col :span="10">
              <el-card>
                <template #header>组织列表</template>
                <el-table :data="organizations" v-loading="orgLoading" border size="small" max-height="420">
                  <el-table-column prop="name" label="名称" />
                  <el-table-column prop="code" label="编码" width="120" />
                  <el-table-column prop="description" label="说明" show-overflow-tooltip />
                </el-table>
              </el-card>
            </el-col>
            <el-col :span="14">
              <el-card>
                <template #header>部门列表</template>
                <el-table :data="departments" v-loading="orgLoading" border size="small" max-height="420">
                  <el-table-column prop="organization_id" label="组织 ID" width="90" />
                  <el-table-column prop="name" label="部门名称" />
                  <el-table-column prop="code" label="编码" width="120" />
                  <el-table-column prop="description" label="说明" show-overflow-tooltip />
                </el-table>
              </el-card>
            </el-col>
          </el-row>

          <el-row :gutter="16" class="system-block-row">
            <el-col :span="10">
              <el-card>
                <template #header>用户归属分配</template>
                <div style="display: grid; gap: 12px">
                  <el-select v-model="userAssignForm.user_id" placeholder="选择用户">
                    <el-option v-for="item in users" :key="item.id" :label="`${item.username} (${item.role})`" :value="item.id" />
                  </el-select>
                  <el-select v-model="userAssignForm.organization_id" placeholder="选择组织">
                    <el-option v-for="item in organizations" :key="item.id" :label="item.name" :value="item.id" />
                  </el-select>
                  <el-select v-model="userAssignForm.department_id" placeholder="选择部门">
                    <el-option v-for="item in departments.filter((row) => !userAssignForm.organization_id || row.organization_id === userAssignForm.organization_id)" :key="item.id" :label="item.name" :value="item.id" />
                  </el-select>
                  <el-input v-model="userAssignForm.job_title" placeholder="岗位名称" />
                  <el-button type="primary" :loading="orgLoading" @click="assignUserOrg">保存归属</el-button>
                </div>
              </el-card>
            </el-col>
            <el-col :span="14">
              <el-card>
                <template #header>用户列表</template>
                <el-table :data="users" v-loading="orgLoading" border size="small" max-height="420">
                  <el-table-column prop="username" label="用户名" />
                  <el-table-column prop="role" label="角色" width="100" />
                  <el-table-column prop="organization_id" label="组织 ID" width="90" />
                  <el-table-column prop="department_id" label="部门 ID" width="90" />
                  <el-table-column prop="job_title" label="岗位" width="120" />
                </el-table>
              </el-card>
            </el-col>
          </el-row>
        </template>
      </el-tab-pane>

      <el-tab-pane label="敏感治理" name="sensitivity">
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
                <el-button text type="primary" @click="openKnowledgeDocument(row)">查看</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="外部连接器" name="connectors">
        <div class="app-section-intro tab-intro">
          <strong>连接器与同步任务</strong>
          <span>维护连接器配置、查看导入概览，并回看每次同步任务的扫描与入库结果。</span>
        </div>

        <el-row :gutter="16" class="system-block-row">
          <el-col :span="10">
            <el-card>
              <template #header>新增连接器</template>
              <div style="display: grid; gap: 12px">
                <el-select v-model="newConnector.connector_type" placeholder="连接器类型">
                  <el-option label="企业网盘" value="drive" />
                  <el-option label="知识库" value="wiki" />
                  <el-option label="法源" value="mailbox" />
                  <el-option label="OA 审批" value="oa_approval" />
                  <el-option label="OneDrive（Microsoft Graph）" value="ms_graph_onedrive" />
                  <el-option label="SharePoint（Microsoft Graph）" value="ms_graph_sharepoint" />
                  <el-option label="ERP（REST API）" value="erp_rest" />
                  <el-option label="CRM（REST API）" value="crm_rest" />
                </el-select>
                <el-input v-model="newConnector.name" placeholder="连接器名称" />
                <el-input v-model="newConnector.config_json" type="textarea" :rows="5" :placeholder="connectorConfigPlaceholder" />
                <template v-if="isEnterpriseConnector(newConnector.connector_type)">
                  <el-radio-group v-if="isMicrosoftConnector(newConnector.connector_type)" v-model="newConnector.graph_auth_mode">
                    <el-radio-button value="access_token">手工访问令牌</el-radio-button>
                    <el-radio-button value="oauth_client_secret">OAuth 应用密钥</el-radio-button>
                  </el-radio-group>
                  <el-input
                    v-model="newConnector.secret"
                    type="password"
                    show-password
                    :placeholder="enterpriseSecretPlaceholder(newConnector.connector_type, newConnector.graph_auth_mode)"
                  />
                  <el-alert
                    type="info"
                    :closable="false"
                    :title="enterpriseConfigHint(newConnector.connector_type, newConnector.graph_auth_mode)"
                  />
                </template>
                <el-button type="primary" :loading="connectorLoading" @click="createConnector">创建连接器</el-button>
              </div>
            </el-card>
          </el-col>
          <el-col :span="14">
            <el-card>
              <template #header>连接器列表</template>
              <el-table :data="connectors" v-loading="connectorLoading" border size="small" max-height="360">
                <el-table-column prop="name" label="名称" />
                <el-table-column prop="connector_type" label="类型" width="120" />
                <el-table-column prop="organization_id" label="组织 ID" width="90" />
                <el-table-column prop="department_id" label="部门 ID" width="90" />
                <el-table-column label="最近同步" width="180">
                  <template #default="{ row }">
                    {{ row.last_sync_at || '-' }}
                  </template>
                </el-table-column>
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{ row }">
                    <el-tag size="small" :type="connectorStatusTagType(row.status)">
                      {{ connectorStatusLabel(row.status) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="导入概览" min-width="220">
                  <template #default="{ row }">
                    <div style="display: grid; gap: 4px">
                      <span>最近导入 {{ row.last_imported_count || 0 }} / 跳过 {{ row.last_skipped_count || 0 }}</span>
                      <span style="color: var(--color-text-muted)">累计导入 {{ row.total_imported_count || 0 }} / 跳过 {{ row.total_skipped_count || 0 }}</span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="180">
                  <template #default="{ row }">
                    <el-button text type="primary" :disabled="connectorLoading" @click="syncConnector(row)">同步</el-button>
                    <el-button text type="info" @click="openConnectorDocuments(row)">文档</el-button>
                    <el-button v-if="isEnterpriseConnector(row.connector_type)" text type="warning" @click="openEnterpriseCredentialDialog(row)">凭据</el-button>
                    <el-button v-if="isMicrosoftConnector(row.connector_type)" text type="success" :disabled="connectorLoading" @click="startMicrosoftOAuth(row)">OAuth 授权</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>

        <el-card class="system-panel-card">
          <template #header>同步任务</template>
          <div class="app-toolbar" style="margin-bottom: 12px">
            <el-select v-model="connectorJobFilter.connector_id" clearable placeholder="按连接器筛选" style="width: 180px" @change="fetchConnectorData">
              <el-option v-for="item in connectors" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <el-select v-model="connectorJobFilter.status" clearable placeholder="按状态筛选" style="width: 160px" @change="fetchConnectorData">
              <el-option label="排队中" value="pending" />
              <el-option label="执行中" value="running" />
              <el-option label="已完成" value="succeeded" />
              <el-option label="失败" value="failed" />
            </el-select>
          </div>
          <el-table :data="connectorJobs" v-loading="connectorLoading" border size="small" max-height="420" :row-class-name="connectorJobRowClassName">
            <el-table-column type="expand" width="60">
              <template #default="{ row }">
                <div style="display: grid; gap: 8px">
                  <div><strong>来源：</strong> {{ connectorJobDetail(row).source || '-' }}</div>
                  <div>
                    <strong>导入：</strong> {{ connectorJobDetail(row).imported_count || 0 }}
                    <span style="margin-left: 12px"><strong>跳过：</strong> {{ connectorJobDetail(row).skipped_count || 0 }}</span>
                    <span style="margin-left: 12px"><strong>扫描：</strong> {{ connectorJobDetail(row).scanned_count || 0 }}</span>
                  </div>
                  <div v-if="connectorJobDetail(row).imported_items?.length || connectorJobDetail(row).imported_titles?.length">
                    <strong>新增文件：</strong>
                    <template v-if="connectorJobDetail(row).imported_items?.length">
                      <el-button
                        v-for="item in connectorJobDetail(row).imported_items"
                        :key="`imported-${item.document_id || item.title}`"
                        text
                        type="primary"
                        size="small"
                        @click="openConnectorDocument(item)"
                      >
                        {{ item.title }}
                      </el-button>
                    </template>
                    <template v-else>
                      {{ connectorJobDetail(row).imported_titles.join('、') }}
                    </template>
                  </div>
                  <div v-if="connectorJobDetail(row).skipped_items?.length || connectorJobDetail(row).skipped_titles?.length">
                    <strong>跳过文件：</strong>
                    <template v-if="connectorJobDetail(row).skipped_items?.length">
                      <el-button
                        v-for="item in connectorJobDetail(row).skipped_items"
                        :key="`skipped-${item.document_id || item.title}`"
                        text
                        type="info"
                        size="small"
                        @click="openConnectorDocument(item)"
                      >
                        {{ item.title }}
                      </el-button>
                    </template>
                    <template v-else>
                      {{ connectorJobDetail(row).skipped_titles.join('、') }}
                    </template>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="connector_id" label="连接器 ID" width="100" />
            <el-table-column prop="sync_mode" label="模式" width="100" />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="connectorStatusTagType(row.status)">
                  {{ connectorStatusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="result_summary" label="结果" show-overflow-tooltip />
            <el-table-column prop="updated_at" label="更新时间" width="180" />
          </el-table>
        </el-card>

        <el-dialog v-model="enterpriseCredentialDialog.visible" title="更新企业连接器凭据" width="440px">
          <div style="display: grid; gap: 12px">
            <span style="color: var(--color-text-secondary)">{{ enterpriseCredentialDialog.connector?.name }}</span>
            <el-radio-group v-if="isMicrosoftConnector(enterpriseCredentialDialog.connector?.connector_type)" v-model="enterpriseCredentialDialog.graph_auth_mode">
              <el-radio-button value="access_token">访问令牌</el-radio-button>
              <el-radio-button value="oauth_client_secret">OAuth 密钥</el-radio-button>
            </el-radio-group>
            <el-input
              v-model="enterpriseCredentialDialog.secret"
              type="password"
              show-password
              :placeholder="enterpriseSecretPlaceholder(enterpriseCredentialDialog.connector?.connector_type, enterpriseCredentialDialog.graph_auth_mode)"
            />
          </div>
          <template #footer>
            <el-button @click="enterpriseCredentialDialog.visible = false">取消</el-button>
            <el-button type="primary" :loading="connectorLoading" @click="saveEnterpriseCredentials">保存凭据</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <el-tab-pane label="任务中心" name="tasks">
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
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="taskDetailVisible" title="任务详情" width="760px" class="system-dialog">
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

    <el-dialog v-model="feedbackResolveVisible" title="处理反馈" width="560px" class="system-dialog">
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

    <el-dialog v-model="qaReplayVisible" title="问答回放" width="760px" class="system-dialog">
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
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElSpace } from 'element-plus/es/components/space/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTabs, ElTabPane } from 'element-plus/es/components/tabs/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
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
import 'element-plus/es/components/tab-pane/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../api'
import { ElMessage } from 'element-plus/es/components/message/index'
import StatusTag from '../components/StatusTag.vue'
import { getStatusLabel } from '../utils/status'
import { useSystemTaskMonitor } from '../composables/useSystemTaskMonitor'
import { useSystemConnectors } from '../composables/useSystemConnectors'
import { useSystemFeedback } from '../composables/useSystemFeedback'
import { useSystemOrganization } from '../composables/useSystemOrganization'
import { useSystemKnowledge } from '../composables/useSystemKnowledge'
import { useSystemApprovals } from '../composables/useSystemApprovals'
import { useSystemObservability } from '../composables/useSystemObservability'
import { useSystemActivity } from '../composables/useSystemActivity'
import { useSystemTokenAnalytics } from '../composables/useSystemTokenAnalytics'
import PilotAnalyticsTabs from '../components/system/PilotAnalyticsTabs.vue'

const route = useRoute()
const router = useRouter()
const emptyGovernance = () => ({ today: {}, rate_limit: {}, policy: {} })
const emptyTokenStats = () => ({ by_action: {}, by_date: {}, by_model: {}, governance: emptyGovernance() })
const emptyGlobalStats = () => ({ by_model: {}, governance: { today: {}, policy: {} } })

const activeTab = ref(['health', 'tokens', 'oplogs', 'alerts', 'experiments', 'feedback', 'funnel', 'retention', 'tool_health', 'approvals', 'knowledge', 'orgs', 'sensitivity', 'connectors', 'tasks'].includes(route.query.tab) ? route.query.tab : 'health')
const currentUser = ref(null)
const focusedConnectorJobId = computed(() => {
  const parsed = Number(route.query.connectorSyncJobId)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
})
const isAdmin = computed(() => currentUser.value?.role === 'admin')
const {
  tokenDays, myStats, globalStats, llmPage, llmPageSize, llmTotal, llmStats, llmCalls,
  llmLoading, billingStats, qaReplayRows, qaReplayVisible, selectedQaReplay, fetchTokenData,
  openQaReplay, resetTokenPagination, handleLlmPageChange,
} = useSystemTokenAnalytics({ client: api, message: ElMessage, isAdmin })

const {
  taskDays, taskScope, taskSource, taskStatus, taskRuns, taskLoading, taskRunPage, taskRunPageSize,
  taskRunTotal, retryingTaskKey, taskDetailVisible, selectedTaskDetail, runningTaskCount,
  failedTaskCount, retryableTaskCount, agentRunCount, agentSucceededCount, agentSuccessRate,
  fetchTaskRuns, resetTaskRunPagination, handleTaskRunPageChange, openTaskTarget, retryTask, showTaskDetail,
} = useSystemTaskMonitor({ client: api, message: ElMessage, router, isAdmin, onTaskRetried: () => fetchAlerts() })

const {
  healthLoading,
  healthData,
  experimentDays,
  experimentLoading,
  experimentOverview,
  toolHealthLoading,
  toolHealthRows,
  fetchHealthData,
  fetchExperimentOverview,
  fetchToolHealth,
} = useSystemObservability({ client: api, message: ElMessage, isAdmin, tokenDays })

const {
  logDays, logModule, logScope, logs, logStats, logLoading, logPage, logPageSize, logTotal,
  alertDays, alertScope, alertSource, alertCategory, alertSeverity, alerts, alertStats,
  alertLoading, alertPage, alertPageSize, alertTotal, fetchLogData, fetchAlerts,
  resetLogPagination, handleLogPageChange, resetAlertPagination, handleAlertPageChange,
} = useSystemActivity({ client: api, message: ElMessage, isAdmin })
const alertCategoryOptions = [
  { label: '模型错误', value: 'model_error' },
  { label: '工具错误', value: 'tool_error' },
  { label: '超时错误', value: 'timeout_error' },
  { label: '权限错误', value: 'permission_error' },
  { label: '数据错误', value: 'data_error' },
  { label: '网络错误', value: 'network_error' },
  { label: '异步任务错误', value: 'async_task_error' },
  { label: 'Agent 错误', value: 'agent_error' },
  { label: '计划任务错误', value: 'scheduler_error' },
  { label: '计划任务延迟', value: 'scheduler_delay' },
  { label: '法源同步错误', value: 'mailbox_sync_error' },
  { label: 'SMTP 外发错误', value: 'outbound_email_error' },
  { label: '外发待审批', value: 'approval_pending' },
  { label: '系统错误', value: 'system_error' },
]

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
    meeting_decision_extract: '会议决策提取',
    meeting_topic_extract: '会议议题提取',
    email_generate: '审查生成',
    email_reply: '审查回复',
    email_tone_switch: '审查风格切换',
    email_thread_summary: '审查线程总结',
    email_polish: '审查润色',
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

const myGovernance = computed(() => ({ ...emptyGovernance(), ...(myStats.value.governance || {}) }))
const globalGovernance = computed(() => ({ today: {}, policy: {}, ...(globalStats.value.governance || {}) }))

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

const experimentArtifacts = computed(() => experimentOverview.value.artifact_status || {})
const experimentSummary = computed(() => experimentOverview.value.summary || {})
const rolloutSummary = computed(() => experimentOverview.value.rollouts || { items: [] })
const experimentRows = computed(() => {
  const rows = experimentOverview.value.experiments || []
  return rows.map((row) => {
    const config = row.effective_config || {}
    const summary = row.summary || {}
    return {
      name: row.name,
      prompt_label: `${config.prompt_template || '-'} / v${config.prompt_version || '-'}`,
      top_k: config.top_k ?? '-',
      citation_accuracy_text: formatRate(summary.citation_accuracy),
      hit_at_k_text: formatRate(summary.hit_at_k),
      refusal_accuracy_text: formatRate(summary.refusal_accuracy),
      badcase_count: row.badcase_count || 0,
      regression_metrics: row.regression_metrics || [],
      config_drift_count: (row.config_drift || []).length,
    }
  })
})
const rolloutRows = computed(() => {
  const rows = rolloutSummary.value.items || []
  return rows
    .filter((row) => row.rollout)
    .map((row) => ({
      name: row.name,
      stable_version: row.active_version_number ? `v${row.active_version_number}` : '-',
      rollout_version: row.rollout?.version_number ? `v${row.rollout.version_number}` : '-',
      percentage_text: row.rollout?.percentage ? `${row.rollout.percentage}%` : '-',
      started_at: row.rollout?.started_at || '-',
    }))
})
const promptTrafficRows = computed(() => {
  const rows = experimentOverview.value.prompt_traffic?.items || []
  return rows.map((row) => ({
    ...row,
    success_rate_text: formatRate(row.calls ? ((row.calls - row.failed_calls) / row.calls) : 0),
  }))
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

const alertCategoryRows = computed(() => {
  const byCategory = alertStats.value.by_category || {}
  return Object.entries(byCategory)
    .map(([key, count]) => ({ key, label: alertCategoryLabel(key), count }))
    .sort((a, b) => b.count - a.count)
})

const alertDateRows = computed(() => {
  const byDate = alertStats.value.by_date || {}
  return Object.entries(byDate)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))
})

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

const moduleLabel = (mod) => ({
  document: '文档',
  meeting: '会议',
  legal: '法律文书',
  task: '任务',
  agent: 'Agent',
  async_task: '异步任务',
  system: '系统',
  chat: '聊天',
  prompt: '提示词',
  auth: '认证',
}[mod] || mod)

const moduleTagType = (mod) => ({
  document: '',
  meeting: 'success',
  legal: 'warning',
  task: 'info',
  agent: 'danger',
  async_task: 'warning',
  system: 'info',
  chat: '',
  prompt: 'success',
  auth: 'warning',
}[mod] || '')

const alertCategoryLabel = (category) => ({
  model_error: '模型错误',
  tool_error: '工具错误',
  timeout_error: '超时错误',
  permission_error: '权限错误',
  data_error: '数据错误',
  network_error: '网络错误',
  async_task_error: '异步任务错误',
  agent_error: 'Agent 错误',
  scheduler_error: '计划任务错误',
  scheduler_delay: '计划任务延迟',
  mailbox_sync_error: '法源同步错误',
  outbound_email_error: 'SMTP 外发错误',
  approval_pending: '外发待审批',
  system_error: '系统错误',
}[category] || category || '未分类')

const alertSeverityLabel = (severity) => ({
  high: '高',
  medium: '中',
  low: '低',
}[severity] || severity || '未知')

const alertSeverityTagType = (severity) => ({
  high: 'danger',
  medium: 'warning',
  low: 'info',
}[severity] || 'info')

const alertSourceTagType = (source) => ({
  agent: 'danger',
  async_task: 'warning',
  scheduler: 'danger',
  mailbox: 'warning',
  outbound_email: 'warning',
}[source] || 'info')

const connectorStatusLabel = (status) => ({
  pending: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  active: '可用',
}[status] || status || '未知')

const connectorStatusTagType = (status) => ({
  pending: 'info',
  running: 'warning',
  succeeded: 'success',
  failed: 'danger',
  active: 'success',
}[status] || 'info')

const connectorJobDetail = (row) => {
  if (!row?.result_detail_json) return {}
  try {
    const payload = JSON.parse(row.result_detail_json)
    return payload && typeof payload === 'object' ? payload : {}
  } catch {
    return {}
  }
}

const feedbackReasonLabel = (reason) => ({
  incorrect_answer: '答案不准确',
  wrong_citation: '引用不准确',
  incomplete_answer: '信息不完整',
  not_helpful: '没有帮助',
}[reason] || reason || '未分类')
const qaSourceLabel = (source) => ({
  document: '文档问答',
  chat: '聊天问答',
  ws_chat: '流式问答',
}[source] || source || '未知')

const healthCheckLabel = (name) => ({
  database: '数据库',
  redis: 'Redis',
  llm_provider: '模型服务',
  ollama: 'Ollama',
}[name] || name)

const healthStatusLabel = (status) => ({
  ok: '正常',
  degraded: '降级',
  error: '异常',
}[status] || '未知')

const healthStatusTagType = (status) => ({
  ok: 'success',
  degraded: 'warning',
  error: 'danger',
}[status] || 'info')

const formatRate = (value) => {
  if (value === null || value === undefined || value === '') return '-'
  return `${Math.round(Number(value) * 100)}%`
}

const syncTabQuery = () => {
  router.replace({ query: { ...route.query, tab: activeTab.value } })
}

const normalizeConnectorFilterId = (value) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

const {
  connectorLoading,
  connectors,
  connectorJobs,
  newConnector,
  enterpriseCredentialDialog,
  connectorJobFilter,
  isEnterpriseConnector,
  isMicrosoftConnector,
  enterpriseSecretPlaceholder,
  enterpriseConfigHint,
  connectorConfigPlaceholder,
  fetchConnectorData,
  connectorJobRowClassName,
  createConnector,
  openEnterpriseCredentialDialog,
  saveEnterpriseCredentials,
  startMicrosoftOAuth,
  syncConnector,
} = useSystemConnectors({
  client: api,
  message: ElMessage,
  isAdmin,
  focusedJobId: focusedConnectorJobId,
  initialConnectorId: Number(route.query.connectorId) || null,
  refreshTasks: fetchTaskRuns,
  refreshAlerts: fetchAlerts,
})

const openApprovalInAgent = (row) => {
  router.push({ path: '/agent', query: { tab: 'approvals', approvalId: String(row.id) } })
}

const openKnowledgeDocument = (row) => {
  router.push({ path: '/documents', query: { documentId: String(row.id) } })
}

const openConnectorDocuments = (row) => {
  if (!row?.id) return
  router.push({ path: '/documents', query: { connectorId: String(row.id) } })
}

const openConnectorDocument = (item) => {
  if (!item?.document_id) return
  router.push({ path: '/documents', query: { documentId: String(item.document_id) } })
}

const {
  feedbackDays, feedbackScope, feedbackValueFilter, feedbackStatusFilter, feedbackRows, feedbackStats,
  feedbackLoading, feedbackPage, feedbackPageSize, feedbackTotal, feedbackResolveVisible,
  selectedFeedback, feedbackResolutionNote, resolvingFeedback, fetchFeedbackData, exportFeedbackBundle,
  resetFeedbackPagination, handleFeedbackPageChange, openFeedbackTarget, openFeedbackResolve,
  submitFeedbackResolve,
} = useSystemFeedback({ client: api, message: ElMessage, router, isAdmin })

const {
  orgLoading,
  organizations,
  departments,
  users,
  newOrg,
  newDepartment,
  userAssignForm,
  fetchOrgData,
  createOrganization,
  createDepartment,
  assignUserOrg,
} = useSystemOrganization({ client: api, message: ElMessage })

const {
  knowledgeLoading,
  knowledgeBases,
  knowledgeDocuments,
  sensitivityLoading,
  sensitiveDocuments,
  fetchKnowledgeData,
  fetchSensitiveDocuments,
} = useSystemKnowledge({ client: api, message: ElMessage })

const { approvalsLoading, approvals, approvalStats, fetchApprovalData } = useSystemApprovals({
  client: api,
  message: ElMessage,
})

onMounted(async () => {
  try {
    const { data } = await api.getMe()
    currentUser.value = data
  } catch {
    currentUser.value = null
  }
  const jobs = [fetchHealthData(), fetchTokenData(), fetchLogData(), fetchAlerts(), fetchFeedbackData(), fetchToolHealth(), fetchApprovalData(), fetchKnowledgeData(), fetchSensitiveDocuments(), fetchConnectorData(), fetchTaskRuns()]
  if (currentUser.value?.role === 'admin') {
    jobs.push(fetchExperimentOverview(), fetchOrgData())
  }
  Promise.allSettled(jobs)
})

watch(
  () => route.query.tab,
  async (value, oldValue) => {
    if (value === oldValue) return
    const nextTab = typeof value === 'string' && ['health', 'tokens', 'oplogs', 'alerts', 'experiments', 'feedback', 'funnel', 'retention', 'tool_health', 'approvals', 'knowledge', 'orgs', 'sensitivity', 'connectors', 'tasks'].includes(value)
      ? value
      : 'health'
    if (nextTab !== activeTab.value) {
      activeTab.value = nextTab
    }
    if (nextTab === 'connectors') {
      await fetchConnectorData()
    }
    if (nextTab === 'tasks') {
      await fetchTaskRuns()
    }
  }
)

watch(
  () => route.query.connectorId,
  async (value, oldValue) => {
    if (value === oldValue) return
    connectorJobFilter.value.connector_id = normalizeConnectorFilterId(value)
    if (activeTab.value === 'connectors') {
      await fetchConnectorData()
    }
  }
)

watch(
  () => route.query.connectorSyncJobId,
  async (value, oldValue) => {
    if (value === oldValue) return
    if (activeTab.value === 'connectors') {
      await fetchConnectorData()
    }
  }
)
</script>

<style scoped>
.system-page {
  display: grid;
  gap: var(--space-5);
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}

.page-heading {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: center;
  padding: var(--space-6);
}
.page-heading h3 {
  margin: 0;
  font-size: var(--text-3xl);
  line-height: 1.15;
  color: var(--color-text);
  letter-spacing: 0;
  font-weight: 800;
}
.page-heading p {
  margin: var(--space-1) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--text-base);
  line-height: 1.6;
}

.system-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.overview-tile {
  padding: var(--space-5) var(--space-5);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.overview-tile:hover {
  box-shadow: var(--shadow-card-hover);
  border-color: var(--color-border-hover);
  transform: translateY(-2px);
}
.overview-tile span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.overview-tile strong {
  font-size: var(--text-3xl);
  line-height: var(--text-3xl-lh);
  color: var(--color-text);
  font-weight: 800;
}

.system-command-bar {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: center;
  flex-wrap: wrap;
  padding: var(--space-5) var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background:
    radial-gradient(circle at 92% 16%, rgba(39, 189, 245, 0.14), transparent 32%),
    var(--gradient-hero);
  box-shadow: var(--shadow-xs);
}

.command-copy {
  display: grid;
  gap: 4px;
}
.command-copy strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}
.command-copy span {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.command-chips {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.command-chip {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-full);
  background: var(--color-primary-light);
  border: 1px solid rgba(79, 106, 245, 0.16);
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-weight: 600;
}

/* ─── Tabs ─── */
:deep(.system-tabs > .el-tabs__header) {
  margin: 0;
  max-width: 100%;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__nav-wrap) {
  padding: var(--space-1);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-xs);
  max-width: 100%;
  overflow: hidden;
}
:deep(.system-tabs),
:deep(.system-tabs .el-tabs__content),
:deep(.system-tabs .el-tab-pane),
:deep(.system-tabs .el-card),
:deep(.system-tabs .el-table) {
  min-width: 0;
  max-width: 100%;
}
:deep(.system-tabs .el-row) {
  max-width: 100%;
}
:deep(.system-tabs .el-table__inner-wrapper) {
  max-width: 100%;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__nav) {
  gap: 4px;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__item) {
  height: 34px;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
}
:deep(.system-tabs > .el-tabs__header .el-tabs__item.is-active) {
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-weight: 700;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__active-bar) {
  display: none;
}
:deep(.system-tabs .el-tab-pane > .el-card:first-child),
:deep(.system-tabs .el-tab-pane > .el-row:first-child) {
  margin-top: var(--space-5);
}

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

:deep(.system-tabs .el-card) {
  border-radius: var(--radius-lg);
}
:deep(.system-tabs .el-card__header) {
  padding-bottom: var(--space-4);
}
:deep(.system-tabs .el-table th) {
  background: var(--color-bg-alt);
}
:deep(.focused-connector-job-row) {
  --el-table-tr-bg-color: #F0F2FF;
}

/* ─── Dialogs ─── */
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


@media (max-width: 1100px) {
  .system-overview {
    grid-template-columns: 1fr 1fr;
  }
  .system-command-bar {
    align-items: flex-start;
  }
  .dialog-metrics {
    grid-template-columns: 1fr;
  }
}
</style>
