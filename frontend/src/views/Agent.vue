<template>
  <div class="agent-page">
    <div class="page-header">
      <div>
        <div class="section-eyebrow">Agent Studio</div>
        <h3>Agent配置</h3>
        <p>输入目标后自动规划并连续执行；仅在创建任务、批量生成待办或查询敏感数据时请求确认。</p>
      </div>
      <div class="header-tips">
        <span>推荐示例：</span>
        <el-button text @click="applyExample('总结会议 1，并把行动项创建成任务')">会议转任务</el-button>
        <el-button text @click="applyExample('查询我未完成的任务，并生成一封催办汇总邮件')">任务转邮件</el-button>
        <el-button text @click="applyExample('总结文档 1，并提取其中的风险点')">文档风险</el-button>
      </div>
    </div>

    <div class="overview-strip">
      <div class="overview-tile">
        <span>待审批动作</span>
        <strong>{{ approvals.length }}</strong>
        <p>高风险工具调用会在这里进入人工确认。</p>
      </div>
      <div class="overview-tile">
        <span>运行历史</span>
        <strong>{{ historyTotal }}</strong>
        <p>可回看执行目标、最终结果和完整步骤链路。</p>
      </div>
      <div class="overview-tile">
        <span>当前模式</span>
        <strong>{{ demoPreset ? '演示链路' : '自由执行' }}</strong>
        <p>{{ demoPreset ? demoPreset.title : '按一句话目标自动规划并执行。' }}</p>
      </div>
    </div>

    <section class="expert-directory" aria-label="专家角色目录">
      <div class="directory-heading">
        <div>
          <div class="section-eyebrow">Expert Roles</div>
          <strong>企业专家协作网络</strong>
        </div>
        <span>总管负责编排，专家负责结论，执行层只处理已确认动作。</span>
      </div>
      <div class="expert-role-grid">
        <article v-for="role in expertRoles" :key="role.agent_type" class="expert-role">
          <div class="expert-role-topline">
            <strong>{{ role.label }}</strong>
            <el-tag size="small" :type="role.execution_mode === 'controlled_side_effect' ? 'warning' : role.execution_mode === 'orchestration_only' ? 'primary' : 'success'">
              {{ executionModeLabel(role.execution_mode) }}
            </el-tag>
          </div>
          <p>{{ role.description }}</p>
          <div class="expert-role-contract">
            <span>交付</span>
            <strong>{{ role.output_contract }}</strong>
          </div>
          <div class="expert-role-tools">
            <span v-for="tool in role.allowed_tools || []" :key="`${role.agent_type}-${tool}`">{{ toolLabel(tool) }}</span>
          </div>
        </article>
      </div>
    </section>

    <div class="agent-command-bar">
      <div class="command-copy">
        <div class="section-eyebrow">Execution Control</div>
        <strong>低风险步骤自动执行，敏感动作按需确认</strong>
        <span>适合处理文档分析、会议转任务、任务同步邮件和跨模块串联动作。</span>
      </div>
      <div class="command-chips">
        <span class="command-chip">最大步数 {{ maxSteps }}</span>
        <span class="command-chip">审批 {{ approvals.length }}</span>
        <span class="command-chip">历史 {{ historyTotal }}</span>
        <span class="command-chip">成功率 {{ agentMetrics.success_rate == null ? '-' : `${Math.round(agentMetrics.success_rate * 100)}%` }}</span>
        <span class="command-chip">工具成功 {{ agentMetrics.reliability?.tool_success_rate == null ? '-' : `${Math.round(agentMetrics.reliability.tool_success_rate * 100)}%` }}</span>
        <span class="command-chip">重试任务 {{ agentMetrics.reliability?.retrying_run_rate == null ? '-' : `${Math.round(agentMetrics.reliability.retrying_run_rate * 100)}%` }}</span>
        <span class="command-chip">人工介入 {{ agentMetrics.reliability?.human_intervention_rate == null ? '-' : `${Math.round(agentMetrics.reliability.human_intervention_rate * 100)}%` }}</span>
      </div>
    </div>

    <div class="command-grid">
      <el-card class="input-card">
      <template #header>一句话目标</template>
      <el-input
        v-model="goal"
        type="textarea"
        :rows="4"
        placeholder="例如：总结会议 1，并把行动项创建成任务；再给我一份结果摘要"
      />
      <div class="input-actions">
        <div class="meta-text">文档分析、检索和草稿生成会直接继续；创建任务、批量落待办及敏感数据查询会在执行到该步骤时请求确认。</div>
        <div class="action-row">
          <el-tag v-if="demoContext.documentId" size="small" type="warning">
            演示文档 {{ demoContext.documentId }}{{ demoContext.documentTitle ? ` · ${demoContext.documentTitle}` : '' }}
          </el-tag>
          <span class="meta-text">最大步数</span>
          <el-input-number v-model="maxSteps" :min="2" :max="10" size="small" />
          <el-button :loading="previewLoading" @click="previewPlan">预览计划</el-button>
          <el-button type="primary" :loading="loading" @click="run">直接执行</el-button>
        </div>
      </div>
      </el-card>

      <el-card v-if="demoPreset" class="demo-card">
        <template #header>
          <div class="card-header-inline">
            <div>
              <div class="section-eyebrow">Preset Context</div>
              <span>{{ demoPreset.title }}</span>
            </div>
            <el-tag size="small" type="warning">标准演示链路</el-tag>
          </div>
        </template>
        <div class="demo-grid">
          <div class="demo-block">
            <div class="panel-title">演示说明</div>
            <div class="panel-content">{{ demoPreset.description }}</div>
          </div>
          <div class="demo-block">
            <div class="panel-title">当前文档</div>
            <div class="panel-content">
              {{ demoPreset.documentId ? `文档 ${demoPreset.documentId}` : '未指定文档' }}
              <span v-if="demoPreset.documentTitle"> · {{ demoPreset.documentTitle }}</span>
            </div>
          </div>
          <div class="demo-block">
            <div class="panel-title">预期工具路径</div>
            <ul class="simple-list">
              <li v-for="(item, index) in demoPreset.expectedFlow" :key="`demo-flow-${index}`">{{ item }}</li>
            </ul>
          </div>
          <div class="demo-block">
            <div class="panel-title">演示检查点</div>
            <ul class="simple-list">
              <li v-for="(item, index) in demoPreset.successCriteria" :key="`demo-success-${index}`">{{ item }}</li>
            </ul>
          </div>
        </div>
        <div class="risk-list">
          <div class="panel-title">执行前建议</div>
          <ul class="simple-list">
            <li v-for="(item, index) in demoPreset.prepChecklist" :key="`demo-prep-${index}`">{{ item }}</li>
          </ul>
        </div>
      </el-card>
    </div>

    <el-card v-if="planPreview" class="preview-card">
      <template #header>
        <div class="card-header-inline">
          <span>执行计划预览</span>
          <el-tag :type="planPreview.can_execute ? 'success' : 'warning'" size="small">
            {{ planPreview.can_execute ? '可执行' : '需补充信息' }}
          </el-tag>
        </div>
      </template>

      <div class="preview-summary">
        <div class="panel-title">计划概述</div>
        <div class="panel-content">{{ planPreview.summary }}</div>
      </div>

      <div class="preview-metrics">
        <div class="metric-item">
          <span class="metric-label">预计步数</span>
          <strong>{{ planPreview.estimated_steps }}</strong>
        </div>
        <div class="metric-item">
          <span class="metric-label">涉及工具</span>
          <strong>{{ planPreview.steps.length }}</strong>
        </div>
      </div>

      <div class="timeline" style="margin-top: 16px">
        <div v-for="step in planPreview.steps" :key="`preview-${step.step}`" class="timeline-item">
          <div class="timeline-marker pending">{{ step.step }}</div>
          <div class="timeline-body">
            <div class="timeline-top">
              <strong>{{ toolLabel(step.tool_name) }}</strong>
            </div>
            <div class="panel-content">{{ step.purpose }}</div>
            <div v-if="Object.keys(step.action_input_preview || {}).length" class="detail-block" style="margin-top: 12px">
              <div class="detail-label">预估参数</div>
              <el-input type="textarea" :rows="3" :model-value="formatJson(step.action_input_preview)" readonly />
            </div>
          </div>
        </div>
      </div>

      <div v-if="planPreview.risks?.length" class="risk-list">
        <div class="panel-title">风险与缺口</div>
        <ul class="simple-list">
          <li v-for="(risk, index) in planPreview.risks" :key="`risk-${index}`">{{ risk }}</li>
        </ul>
      </div>

      <div class="preview-actions">
        <el-button type="primary" :loading="loading" :disabled="!planPreview.can_execute" @click="executeRun">立即执行</el-button>
      </div>
    </el-card>

    <el-dialog v-model="sensitiveApprovalVisible" title="确认敏感操作" width="560px" class="agent-dialog" :close-on-click-modal="false">
      <template v-if="activeApproval">
        <div class="preview-summary">
          <div class="panel-title">即将执行</div>
          <div class="panel-content">{{ toolLabel(activeApproval.tool_name) }}</div>
        </div>
        <div class="risk-list">
          <div class="panel-title">操作说明</div>
          <div class="panel-content">该步骤会创建内部任务、批量落待办或读取敏感数据。确认后，Agent 将从当前步骤继续执行。</div>
        </div>
        <div class="detail-block">
          <div class="detail-label">执行参数</div>
          <el-input type="textarea" :rows="5" :model-value="formatJson(activeApproval.input_params)" readonly />
        </div>
      </template>
      <template #footer>
        <el-button :disabled="loading" @click="decideApproval(activeApproval, false)">拒绝操作</el-button>
        <el-button type="primary" :loading="loading" @click="decideApproval(activeApproval, true)">确认并继续</el-button>
      </template>
    </el-dialog>

    <div v-if="runResult" class="summary-grid">
      <el-card class="summary-card">
        <template #header>
          <div class="card-header-inline">
            <span>执行结果</span>
            <StatusTag kind="agent" :status="runResult.status" />
          </div>
        </template>
        <div class="run-hero">
          <div class="run-hero-copy">
            <span class="metric-label">执行目标</span>
            <strong>{{ runResult.goal || goal || '未记录目标' }}</strong>
            <p>{{ finalAnswer || runResult.error || '等待执行结果返回。' }}</p>
          </div>
        </div>
        <div class="summary-metrics">
          <div class="metric-item">
            <span class="metric-label">运行 ID</span>
            <strong>{{ runResult.id || runResult.run_id }}</strong>
          </div>
          <div class="metric-item">
            <span class="metric-label">总步数</span>
            <strong>{{ runResult.total_steps || logs.length || 0 }}</strong>
          </div>
          <div class="metric-item">
            <span class="metric-label">失败原因</span>
            <strong>{{ runResult.failure_reason || '无' }}</strong>
          </div>
        </div>
        <div v-if="finalAnswer" class="answer-panel">
          <div class="panel-title">最终答复</div>
          <div class="panel-content">{{ finalAnswer }}</div>
        </div>
        <div v-if="runResult.error" class="error-panel">
          <div class="panel-title">系统错误</div>
          <div class="panel-content">{{ runResult.error }}</div>
        </div>
        <div v-if="['running', 'cancelling', 'awaiting_approval'].includes(runResult.status)" class="card-actions">
          <el-button type="danger" plain :loading="cancelling" @click="cancelCurrentRun">取消执行</el-button>
        </div>
      </el-card>

      <el-card class="summary-card" v-if="logs.length">
        <template #header>步骤时间线</template>
        <div class="timeline">
          <div v-for="log in logs" :key="log.id" class="timeline-item">
            <div class="timeline-marker" :class="log.status">{{ log.step }}</div>
            <div class="timeline-body">
              <div class="timeline-top">
                <strong>{{ toolLabel(log.tool_name) }}</strong>
                <span class="timeline-meta">{{ actionTypeText(log.action_type) }} · {{ log.duration_ms || 0 }}ms</span>
              </div>
              <div class="timeline-status">
                <el-tag size="small" :type="log.status === 'success' ? 'success' : log.status === 'pending' ? 'warning' : 'danger'">
                  {{ log.status === 'success' ? '成功' : log.status === 'pending' ? '执行中' : '失败' }}
                </el-tag>
                <span v-if="log.error" class="error-inline">{{ log.error }}</span>
              </div>
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <el-card v-if="supervisorPlan.workers?.length" class="details-card supervisor-plan-card">
      <template #header>
        <div class="card-header-inline">
          <div>
            <div class="section-eyebrow">Supervisor Plan</div>
            <span>Worker 编排</span>
          </div>
          <el-tag size="small" :type="supervisorPlan.plan_source === 'llm' ? 'success' : 'warning'">
            {{ supervisorPlan.plan_source === 'llm' ? '模型计划' : '规则回退' }}
          </el-tag>
          <el-tag v-if="supervisorPlan.execution_mode === 'parallel_read_only'" size="small" type="primary">只读并行</el-tag>
        </div>
      </template>
      <div class="supervisor-summary">
        <div>
          <span>意图</span>
          <strong>{{ supervisorPlan.intent || '未识别' }}</strong>
        </div>
        <div>
          <span>风险等级</span>
          <el-tag size="small" :type="supervisorPlan.risk_level === 'high' ? 'danger' : supervisorPlan.risk_level === 'medium' ? 'warning' : 'success'">
            {{ supervisorPlan.risk_level || 'low' }}
          </el-tag>
        </div>
        <div>
          <span>预期产物</span>
          <strong>{{ (supervisorPlan.expected_artifacts || []).join(' · ') || '无' }}</strong>
        </div>
      </div>
      <div class="worker-route" aria-label="Worker 执行顺序">
        <template v-for="(worker, index) in supervisorPlan.workers" :key="`worker-route-${worker}-${index}`">
          <div class="worker-node">
            <span>{{ index + 1 }}</span>
            <strong>{{ workerLabel(worker) }}</strong>
          </div>
          <span v-if="index < supervisorPlan.workers.length - 1" class="worker-route-arrow" aria-hidden="true">{{ supervisorPlan.execution_mode === 'parallel_read_only' ? '+' : '->' }}</span>
        </template>
      </div>
      <div v-if="supervisorPlan.handoffs?.length" class="handoff-list">
        <div v-for="(handoff, index) in supervisorPlan.handoffs" :key="`handoff-${index}`" class="handoff-row">
          <span>{{ workerLabel(handoff.from_worker) }}</span>
          <span class="worker-route-arrow">-></span>
          <strong>{{ workerLabel(handoff.to_worker) }}</strong>
          <span>{{ handoff.completion_summary }}</span>
        </div>
      </div>
      <div v-if="supervisorPlan.aggregation" class="aggregation-panel">
        <strong>{{ supervisorPlan.aggregation.conclusion }}</strong>
        <ul v-if="supervisorPlan.aggregation.findings?.length" class="simple-list">
          <li v-for="(item, index) in supervisorPlan.aggregation.findings" :key="`aggregation-${index}`">{{ item.title }}<span v-if="item.evidence">：{{ item.evidence }}</span></li>
        </ul>
      </div>
      <div v-if="supervisorPlan.execution_mode === 'parallel_read_only'" class="stack-foot">仅文档、会议等白名单只读能力可并发执行；写入、草稿和敏感查询仍按审批串行执行。</div>
      <div v-if="supervisorPlan.fallback_reason" class="stack-foot">回退原因：{{ supervisorPlan.fallback_reason }}</div>
    </el-card>

    <el-card v-if="hasArtifacts" class="details-card">
      <template #header>
        <div class="card-header-inline">
          <span>本次产出对象</span>
          <el-tag size="small" type="success">执行闭环</el-tag>
        </div>
      </template>
      <div class="artifact-summary">
        <div class="artifact-summary-item">
          <span>文档</span>
          <strong>{{ artifactGroups.documents.length }}</strong>
        </div>
        <div class="artifact-summary-item">
          <span>任务</span>
          <strong>{{ artifactGroups.tasks.length }}</strong>
        </div>
        <div class="artifact-summary-item">
          <span>邮件</span>
          <strong>{{ artifactGroups.emails.length }}</strong>
        </div>
        <div class="artifact-summary-item">
          <span>会议</span>
          <strong>{{ artifactGroups.meetings.length }}</strong>
        </div>
      </div>
      <div class="artifact-grid">
        <div v-if="artifactGroups.documents.length" class="artifact-block">
          <div class="panel-title">文档结果</div>
          <div v-for="item in artifactGroups.documents" :key="`doc-${item.document_id}`" class="stack-item">
            <div class="stack-top">
              <strong>文档 {{ item.document_id }}</strong>
              <el-tag size="small">{{ toolLabel(item.tool_name) }}</el-tag>
            </div>
            <p v-if="item.summary">{{ item.summary }}</p>
            <div class="stack-foot">
              <span v-if="item.risk_count">风险数：{{ item.risk_count }}</span>
              <span v-if="item.chunk_count">命中片段：{{ item.chunk_count }}</span>
            </div>
            <el-button v-if="item.document_id" text type="primary" @click="openDocument(item.document_id)">查看文档</el-button>
          </div>
        </div>

        <div v-if="artifactGroups.tasks.length" class="artifact-block">
          <div class="panel-title">创建任务</div>
          <div v-for="item in artifactGroups.tasks" :key="`task-${item.task_id}`" class="stack-item">
            <div class="stack-top">
              <strong>{{ item.title || `任务 ${item.task_id}` }}</strong>
              <el-tag size="small" type="primary">{{ item.status || 'todo' }}</el-tag>
            </div>
            <div class="stack-foot">
              <span v-if="item.assignee">负责人：{{ item.assignee }}</span>
              <span v-if="item.priority">优先级：{{ item.priority }}</span>
            </div>
            <el-button v-if="item.task_id" text type="primary" @click="openTask(item.task_id)">查看任务</el-button>
          </div>
        </div>

        <div v-if="artifactGroups.emails.length" class="artifact-block">
          <div class="panel-title">文书草稿</div>
          <div v-for="item in artifactGroups.emails" :key="`email-${item.draft_id}`" class="stack-item">
            <div class="stack-top">
              <strong>{{ item.subject || `草稿 ${item.draft_id}` }}</strong>
              <el-tag size="small" type="warning">draft</el-tag>
            </div>
            <div class="stack-foot">
              <span v-if="item.recipient">收件人：{{ item.recipient }}</span>
              <span v-if="item.purpose">目的：{{ item.purpose }}</span>
            </div>
            <el-button text type="primary" @click="openEmailDraft(item.draft_id)">查看草稿</el-button>
          </div>
        </div>

        <div v-if="artifactGroups.meetings.length" class="artifact-block">
          <div class="panel-title">会议结果</div>
          <div v-for="item in artifactGroups.meetings" :key="`meeting-${item.meeting_id}`" class="stack-item">
            <div class="stack-top">
              <strong>{{ item.theme || `会议 ${item.meeting_id}` }}</strong>
              <el-tag size="small">{{ toolLabel(item.tool_name) }}</el-tag>
            </div>
            <div class="stack-foot">
              <span v-if="item.action_item_count">行动项：{{ item.action_item_count }}</span>
              <span v-if="item.task_count">创建任务：{{ item.task_count }}</span>
            </div>
            <el-button v-if="item.meeting_id" text type="primary" @click="openMeeting(item.meeting_id)">查看会议</el-button>
          </div>
        </div>
      </div>
    </el-card>

    <el-card v-if="logs.length" class="details-card">
      <template #header>
        <div class="card-header-inline">
          <span>执行详情</span>
          <el-tag size="small">{{ logs.length }} 步</el-tag>
        </div>
      </template>
      <div class="app-section-intro compact-intro">
        <strong>步骤级执行回放</strong>
        <span>展开可查看每一步的决策思路、工具参数、执行观察和原始决策载荷。</span>
      </div>
      <el-collapse>
        <el-collapse-item v-for="log in logs" :key="log.id" :name="log.id">
          <template #title>
            <div class="collapse-title">
              <el-tag size="small" :type="log.status === 'success' ? 'success' : 'danger'">Step {{ log.step }}</el-tag>
              <strong>{{ toolLabel(log.tool_name) }}</strong>
              <span class="timeline-meta">{{ actionTypeText(log.action_type) }}</span>
            </div>
          </template>

          <div class="detail-block" v-if="log.thought">
            <div class="detail-label">决策思路</div>
            <div class="detail-value">{{ log.thought }}</div>
          </div>

          <div class="detail-block" v-if="log.error">
            <div class="detail-label">失败原因</div>
            <div class="detail-value error-text">{{ log.error }}</div>
          </div>

          <div class="detail-block">
            <div class="detail-label">输入参数</div>
            <el-input type="textarea" :rows="4" :model-value="formatJson(log.input_params)" readonly />
          </div>

          <div class="detail-block" v-if="log.observation">
            <div class="detail-label">执行观察</div>
            <el-input type="textarea" :rows="5" :model-value="formatJson(log.observation)" readonly />
          </div>

          <div class="detail-block" v-if="log.raw_decision">
            <div class="detail-label">原始决策</div>
            <el-input type="textarea" :rows="5" :model-value="formatJson(log.raw_decision)" readonly />
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <el-card class="history-card">
      <template #header>
        <div class="card-header-inline">
          <div>
            <div class="section-eyebrow">Human Review</div>
            <span>待审批操作</span>
          </div>
          <el-button size="small" @click="fetchApprovals">刷新</el-button>
        </div>
      </template>
      <div v-if="approvals.length" class="stack-list">
        <div v-for="item in approvals" :key="`approval-${item.id}`" class="stack-item">
          <div class="stack-top">
            <strong>{{ toolLabel(item.tool_name) }}</strong>
            <el-tag size="small" type="warning">{{ item.risk_level }}</el-tag>
            <el-tag size="small" :type="item.status === 'pending' ? 'warning' : item.status === 'approved' ? 'success' : 'danger'">
              {{ item.status === 'pending' ? '待审批' : item.status === 'approved' ? '已通过' : '已拒绝' }}
            </el-tag>
          </div>
          <div class="stack-foot">
            <span v-if="item.agent_type">Agent：{{ item.agent_type }}</span>
            <span>{{ item.created_at }}</span>
          </div>
          <el-input type="textarea" :rows="3" :model-value="formatJson(item.input_params)" readonly />
          <div class="timeline-status" v-if="item.status === 'pending'">
            <el-button size="small" type="success" @click="decideApproval(item, true)">通过</el-button>
            <el-button size="small" type="danger" plain @click="decideApproval(item, false)">拒绝</el-button>
          </div>
          <div v-else-if="item.decision_note" class="stack-foot">备注：{{ item.decision_note }}</div>
        </div>
      </div>
      <el-empty v-else description="暂无待审批操作" />
    </el-card>

    <el-card class="history-card">
      <template #header>
        <div class="card-header-inline">
          <div>
            <div class="section-eyebrow">Run History</div>
            <span>运行历史</span>
          </div>
          <el-button size="small" @click="fetchHistory">刷新</el-button>
        </div>
      </template>
      <el-table :data="history" v-loading="historyLoading" border size="small">
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
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCollapse, ElCollapseItem } from 'element-plus/es/components/collapse/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElInputNumber } from 'element-plus/es/components/input-number/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/collapse/style/css'
import 'element-plus/es/components/collapse-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../api'
import StatusTag from '../components/StatusTag.vue'
import { AGENT_DEMO_PRESETS, buildDocumentRiskGoal, getAgentDemoPreset } from '../utils/agentDemo'
import { actionTypeText, executionModeLabel, formatJson, toolLabel, workerLabel } from '../utils/workspacePresentation'
import { agentSocketUrl, openAgentSocket } from '../utils/agentSocket'

const route = useRoute()
const router = useRouter()
const goal = ref('')
const maxSteps = ref(5)
const loading = ref(false)
const previewLoading = ref(false)
const runResult = ref(null)
const logs = ref([])
const history = ref([])
const approvals = ref([])
const agentMetrics = ref({})
const agentRegistry = ref([])
const supervisorRole = ref(null)
const cancelling = ref(false)
const historyLoading = ref(false)
const historyPage = ref(1)
const historyPageSize = ref(10)
const historyTotal = ref(0)
const planPreview = ref(null)
const planPreviewSignature = ref('')
const sensitiveApprovalVisible = ref(false)
const activeApproval = ref(null)
const demoContext = ref({
  type: '',
  documentId: null,
  documentTitle: '',
})
let agentWs = null

const finalAnswer = computed(() => runResult.value?.final_answer || runResult.value?.result || '')
const demoPreset = computed(() => getAgentDemoPreset(demoContext.value.type, demoContext.value))
const artifactGroups = computed(() => {
  const a = runResult.value?.artifacts || {}
  // 后端 artifacts 可能缺失部分 key（旧数据/异常结果），逐 key 兜底避免模板 .length 崩溃
  return {
    documents: Array.isArray(a.documents) ? a.documents : [],
    meetings: Array.isArray(a.meetings) ? a.meetings : [],
    tasks: Array.isArray(a.tasks) ? a.tasks : [],
    emails: Array.isArray(a.emails) ? a.emails : [],
  }
})
const supervisorPlan = computed(() => runResult.value?.supervisor_plan || {})
const hasArtifacts = computed(() =>
  ['documents', 'meetings', 'tasks', 'emails'].some((key) => (artifactGroups.value[key] || []).length)
)
const expertRoles = computed(() => [
  ...(supervisorRole.value ? [supervisorRole.value] : []),
  ...agentRegistry.value,
])

const applyExample = (value) => {
  goal.value = value
  planPreview.value = null
  planPreviewSignature.value = ''
  sensitiveApprovalVisible.value = false
  activeApproval.value = null
  demoContext.value = {
    type: '',
    documentId: null,
    documentTitle: '',
  }
}

const bindRunData = (data) => {
  runResult.value = data
  logs.value = data.logs || []
}

const buildAgentWsUrl = () => {
  return agentSocketUrl()
}

const closeAgentWs = () => {
  if (!agentWs) return
  try {
    agentWs.close(1000)
  } catch {}
  agentWs = null
}

const runAgentViaSocket = async (payload, { resetResult = true } = {}) => {
  loading.value = true
  if (resetResult) {
    runResult.value = null
    logs.value = []
  }
  closeAgentWs()

  await new Promise((resolve) => {
    let finished = false
    const token = localStorage.getItem('token')
    if (!token) {
      loading.value = false
      ElMessage.error('登录状态已失效')
      resolve()
      return
    }
    try {
      agentWs = openAgentSocket({
        payload,
        onMessage: async (data) => {

      if (data.type === 'run_started') {
        runResult.value = {
          run_id: data.run_id,
          goal: data.goal,
          status: data.status,
          created_at: data.created_at,
        }
        return
      }

      if (data.type === 'run_resumed') {
        if (runResult.value) {
          runResult.value = {
            ...runResult.value,
            run_id: data.run_id,
            status: 'running',
          }
        }
        return
      }

      if (data.type === 'step_started') {
        logs.value = [
          ...logs.value.filter((item) => !(String(item.id).startsWith('pending-') && item.step === data.step)),
          {
            id: `pending-${data.step}`,
            agent_run_id: runResult.value?.run_id,
            step: data.step,
            action_type: data.action_type,
            thought: data.thought,
            tool_name: data.tool_name,
            input_params: data.input_params ? JSON.stringify(data.input_params, null, 2) : '',
            raw_decision: null,
            observation: null,
            output_result: null,
            status: 'pending',
            error: null,
            duration_ms: null,
            created_at: new Date().toISOString(),
          },
        ].sort((a, b) => (a.step || 0) - (b.step || 0))
        return
      }

      if (data.type === 'step_completed') {
        const nextLog = data.log
        logs.value = [
          ...logs.value.filter((item) => !(String(item.id).startsWith('pending-') && item.step === nextLog.step)),
          nextLog,
        ].sort((a, b) => (a.step || 0) - (b.step || 0))
        return
      }

      if (data.type === 'run_completed' || data.type === 'run_failed' || data.type === 'run_waiting_approval') {
        runResult.value = {
          ...runResult.value,
          ...data.run,
          logs: logs.value,
        }
        loading.value = false
        if (!finished) {
          finished = true
          if (data.type === 'run_completed') ElMessage.success('Agent 执行完成')
          if (data.type === 'run_waiting_approval') {
            ElMessage.warning('检测到敏感操作，请确认后继续执行')
          }
          await fetchApprovals()
          if (data.type === 'run_waiting_approval') openSensitiveApproval(data.approval_request_id)
          await fetchHistory()
          resolve()
        }
        return
      }

      if (data.type === 'run_snapshot') {
        bindRunData({
          ...data.run,
          logs: data.logs,
        })
        loading.value = false
        if (!finished) {
          finished = true
          await fetchApprovals()
          await fetchHistory()
          resolve()
        }
        return
      }

      if (data.type === 'error') {
        loading.value = false
        ElMessage.error(data.message || '执行失败')
        if (!finished) {
          finished = true
          resolve()
        }
      }
        },
        onError: () => {
      loading.value = false
      ElMessage.error('Agent 实时连接异常')
      if (!finished) {
        finished = true
        resolve()
      }
        },
        onClose: () => {
      if (!finished) {
        loading.value = false
        finished = true
        resolve()
      }
        },
      })
    } catch (error) {
      loading.value = false
      ElMessage.error(error.message === 'AUTH_TOKEN_MISSING' ? '登录状态已失效' : 'Agent 实时连接异常')
      if (!finished) { finished = true; resolve() }
    }
  })
}

const syncQueryState = () => {
  const updates = { ...route.query }
  if (goal.value?.trim()) {
    updates.retryGoal = goal.value.trim()
  }
  if (maxSteps.value) {
    updates.maxSteps = String(maxSteps.value)
  }
  router.replace({ query: updates })
}

const currentPlanSignature = () => `${goal.value.trim()}::${maxSteps.value}`

const applyDemoFromRoute = () => {
  const demo = String(route.query.demo || '')
  if (demo !== 'document_risk') {
    demoContext.value = {
      type: '',
      documentId: null,
      documentTitle: '',
    }
    return
  }
  const documentId = Number(route.query.documentId)
  const documentTitle = String(route.query.documentTitle || '')
  if (!Number.isFinite(documentId) || documentId <= 0) return
  demoContext.value = {
    type: demo,
    documentId,
    documentTitle,
  }
  goal.value = buildDocumentRiskGoal(documentId)
  maxSteps.value = AGENT_DEMO_PRESETS.document_risk.maxSteps
}

const previewPlan = async () => {
  if (!goal.value.trim()) {
    ElMessage.warning('请输入目标')
    return
  }
  previewLoading.value = true
  try {
    const { data } = await api.previewAgentPlan(goal.value.trim(), maxSteps.value)
    planPreview.value = data
    planPreviewSignature.value = currentPlanSignature()
  } catch (e) {
    planPreview.value = null
    planPreviewSignature.value = ''
    ElMessage.error(e.response?.data?.detail || '计划预览失败')
  } finally {
    previewLoading.value = false
  }
}

const run = async () => {
  if (!goal.value.trim()) {
    ElMessage.warning('请输入目标')
    return
  }
  await executeRun()
}

const executeRun = async () => {
  if (!goal.value.trim()) {
    ElMessage.warning('请输入目标')
    return
  }
  await runAgentViaSocket({
    action: 'run',
    goal: goal.value,
    max_steps: maxSteps.value,
  })
}

const viewRun = async (row) => {
  try {
    const { data } = await api.getAgentRun(row.id)
    bindRunData(data)
    planPreview.value = null
    planPreviewSignature.value = ''
    sensitiveApprovalVisible.value = false
    activeApproval.value = null
    await fetchApprovals()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '运行记录加载失败')
  }
}

const fetchApprovals = async () => {
  try {
    const { data } = await api.listApprovals({ status: 'pending' })
    approvals.value = data || []
  } catch {
    approvals.value = []
  }
}

const openSensitiveApproval = (approvalId) => {
  const approval = approvals.value.find((item) => item.id === Number(approvalId))
  if (!approval || approval.status !== 'pending') return
  activeApproval.value = approval
  sensitiveApprovalVisible.value = true
}

const decideApproval = async (item, approved) => {
  try {
    if (approved) {
      sensitiveApprovalVisible.value = false
      await runAgentViaSocket(
        {
          action: 'resume_approval',
          approval_id: item.id,
        },
        { resetResult: false }
      )
    } else {
      await api.decideApproval(item.id, {
        approved: false,
        decision_note: '用户拒绝敏感操作',
      })
      if (activeApproval.value?.id === item.id) {
        sensitiveApprovalVisible.value = false
        activeApproval.value = null
      }
      ElMessage.success('审批已拒绝')
    }
    await fetchApprovals()
    await fetchHistory()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '审批失败')
  }
}

const openEmailDraft = () => {
  ElMessage.info('邮件草稿查看页面已下线，产出仍记录在本条执行历史中')
}

const openDocument = (documentId) => {
  router.push({ path: '/documents', query: { documentId: String(documentId) } })
}

const openTask = (taskId) => {
  router.push({ path: '/tasks', query: { taskId: String(taskId), view: 'table' } })
}

const openMeeting = () => {
  ElMessage.info('会议查看页面已下线，产出仍记录在本条执行历史中')
}

const fetchHistory = async () => {
  historyLoading.value = true
  try {
    const { data } = await api.listAgentRuns({
      page: historyPage.value,
      page_size: historyPageSize.value,
    })
    history.value = data?.items || []
    historyTotal.value = data?.total || 0
  } catch {
    history.value = []
    historyTotal.value = 0
  } finally {
    historyLoading.value = false
  }
}

const fetchAgentMetrics = async () => {
  try { const { data } = await api.getAgentMetrics(); agentMetrics.value = data || {} } catch { agentMetrics.value = {} }
}

const fetchAgentRegistry = async () => {
  try {
    const { data } = await api.getAgentRegistry()
    supervisorRole.value = data?.supervisor || null
    agentRegistry.value = data?.items || []
  } catch {
    supervisorRole.value = null
    agentRegistry.value = []
  }
}

const cancelCurrentRun = async () => {
  const runId = runResult.value?.id || runResult.value?.run_id
  if (!runId) return
  cancelling.value = true
  try {
    const { data } = await api.cancelAgentRun(runId, '用户在 Agent 工作台取消执行')
    runResult.value = { ...runResult.value, ...data }
    ElMessage.success(data.status === 'cancelled' ? '执行已取消' : '已请求取消，将在当前步骤结束后停止')
    await fetchHistory(); await fetchAgentMetrics()
  } catch (error) { ElMessage.error(error.response?.data?.detail || '取消执行失败') } finally { cancelling.value = false }
}

const handleHistoryPageChange = async (page) => {
  historyPage.value = page
  await fetchHistory()
  await fetchAgentMetrics()
}

const loadRunFromRoute = async (rawRunId) => {
  const runId = Number(rawRunId)
  if (!Number.isFinite(runId) || runId <= 0) return
  const row = history.value.find((item) => item.id === runId)
  if (row) {
    await viewRun(row)
    return
  }
  try {
    const { data } = await api.getAgentRun(runId)
    bindRunData(data)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '运行记录加载失败')
  }
}

onMounted(async () => {
  applyDemoFromRoute()
  if (route.query.retryGoal) {
    goal.value = String(route.query.retryGoal)
  }
  if (route.query.maxSteps) {
    const parsed = Number(route.query.maxSteps)
    if (Number.isFinite(parsed) && parsed >= 2 && parsed <= 10) {
      maxSteps.value = parsed
    }
  }
  await fetchApprovals()
  await fetchHistory()
  await fetchAgentMetrics()
  await fetchAgentRegistry()
  await loadRunFromRoute(route.query.runId)
})

onUnmounted(() => {
  closeAgentWs()
})

watch(() => route.query.runId, async (value, oldValue) => {
  if (value === oldValue) return
  await loadRunFromRoute(value)
})

watch(
  () => [route.query.demo, route.query.documentId, route.query.documentTitle],
  () => {
    applyDemoFromRoute()
  }
)

watch([goal, maxSteps], syncQueryState)

watch([goal, maxSteps], () => {
  planPreview.value = null
  planPreviewSignature.value = ''
})

watch(loading, (value) => {
  if (!value) {
    closeAgentWs()
  }
})
</script>

<style scoped>
.agent-page {
  display: grid;
  gap: var(--space-6);
  max-width: 1600px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  gap: var(--space-6);
  align-items: flex-start;
  padding: var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--gradient-hero);
  box-shadow: var(--shadow-xs);
}
.page-header h3 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-3xl);
  color: var(--color-text);
  letter-spacing: 0;
  font-weight: 800;
}
.page-header p,
.header-tips {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: var(--text-sm);
}

.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.header-tips {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.overview-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
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
  color: var(--color-text);
  font-size: var(--text-2xl);
  line-height: var(--text-2xl-lh);
  font-weight: 800;
}
.overview-tile p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}

.expert-directory {
  padding: var(--space-5) var(--space-6) var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}
.directory-heading {
  display: flex;
  justify-content: space-between;
  gap: var(--space-5);
  align-items: end;
  margin-bottom: var(--space-4);
}
.directory-heading strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}
.directory-heading > span {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}
.expert-role-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}
.expert-role {
  display: grid;
  align-content: start;
  gap: var(--space-3);
  min-height: 206px;
  padding: var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: #fff;
}
.expert-role-topline {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  align-items: center;
}
.expert-role-topline strong {
  color: var(--color-text);
  font-size: var(--text-sm);
}
.expert-role p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.65;
}
.expert-role-contract {
  display: grid;
  gap: 2px;
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}
.expert-role-contract span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.expert-role-contract strong {
  color: var(--color-text);
  font-size: var(--text-xs);
  font-weight: 600;
  line-height: 1.55;
}
.expert-role-tools {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.expert-role-tools span {
  padding: 3px 6px;
  border-radius: 4px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-size: 11px;
  line-height: 1.3;
}

.agent-command-bar {
  display: flex;
  justify-content: space-between;
  gap: var(--space-5);
  align-items: center;
  flex-wrap: wrap;
  padding: var(--space-5) var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background:
    radial-gradient(circle at 90% 20%, rgba(39, 189, 245, 0.14), transparent 32%),
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

.command-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(320px, 0.9fr);
  gap: var(--space-6);
  align-items: start;
}

.input-card,
.preview-card,
.details-card,
.history-card,
.summary-card,
.demo-card {
  border-radius: var(--radius-lg);
}

.input-actions {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: center;
  margin-top: var(--space-5);
}
.action-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.meta-text {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}

.summary-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: var(--space-6);
}
.summary-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}

.run-hero {
  margin-bottom: var(--space-4);
  padding: var(--space-5);
  border-radius: var(--radius-xl);
  background: var(--gradient-hero);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-xs);
}
.run-hero-copy {
  display: grid;
  gap: var(--space-2);
}
.run-hero-copy strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.run-hero-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.card-header-inline {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  align-items: center;
}

.preview-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-4);
}

.metric-item {
  background: #ffffff;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-xs);
  padding: var(--space-4) var(--space-4);
}
.metric-label {
  display: block;
  margin-bottom: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

.answer-panel,
.error-panel {
  margin-top: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
}
.preview-summary,
.preview-actions,
.risk-list {
  margin-top: var(--space-4);
}
.answer-panel {
  background: var(--color-success-light);
}
.error-panel {
  background: var(--color-danger-light);
}

.panel-title,
.detail-label {
  margin-bottom: var(--space-2);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text);
}
.panel-content,
.detail-value {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--color-text);
}

.timeline {
  display: grid;
  gap: var(--space-4);
}
.timeline-item {
  display: grid;
  grid-template-columns: 40px 1fr;
  gap: var(--space-3);
  align-items: flex-start;
}
.timeline-marker {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-full);
  display: grid;
  place-items: center;
  color: #fff;
  font-weight: 700;
  font-size: var(--text-sm);
  background: var(--color-text-muted);
  transition: all var(--transition-fast);
}
.timeline-marker.success {
  background: var(--color-success);
}
.timeline-marker.pending {
  background: var(--color-accent);
}
.timeline-marker.error {
  background: var(--color-danger);
}
.timeline-body {
  padding: var(--space-3) var(--space-3);
  border-radius: var(--radius-md);
  background: #ffffff;
  border: 1px solid var(--color-border-light);
}
.timeline-body:hover {
  background: var(--color-surface);
  border-color: var(--color-border-hover);
  box-shadow: var(--shadow-sm);
}
.timeline-top,
.timeline-status,
.collapse-title {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.timeline-meta {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

.demo-grid,
.artifact-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.artifact-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.artifact-summary-item {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: #ffffff;
  display: grid;
  gap: var(--space-1);
}
.artifact-summary-item span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.artifact-summary-item strong {
  color: var(--color-text);
  font-size: var(--text-2xl);
  font-weight: 800;
}

.supervisor-plan-card {
  border-top: 3px solid var(--color-primary);
}
.supervisor-summary {
  display: grid;
  grid-template-columns: minmax(180px, 2fr) minmax(120px, 1fr) minmax(160px, 1fr);
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.supervisor-summary > div {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  background: var(--color-bg-alt);
  border-radius: var(--radius-md);
}
.supervisor-summary span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.supervisor-summary strong {
  color: var(--color-text);
  line-height: 1.5;
}
.worker-route {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  overflow-x: auto;
  padding: var(--space-2) 0;
}
.worker-node {
  min-width: 156px;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  background: #ffffff;
  border-radius: var(--radius-md);
}
.worker-node > span {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  color: #ffffff;
  background: var(--color-primary);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: 700;
}
.worker-route-arrow {
  flex: 0 0 auto;
  color: var(--color-text-muted);
  font-family: monospace;
}
.handoff-list {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
.handoff-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-3);
  border-left: 3px solid var(--color-primary-subtle);
  background: var(--color-bg-alt);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.handoff-row strong {
  color: var(--color-text);
}
.aggregation-panel {
  display: grid;
  gap: var(--space-3);
  margin-top: var(--space-4);
  padding: var(--space-3);
  border-left: 3px solid var(--color-primary);
  background: var(--color-bg-alt);
  color: var(--color-text);
}

.compact-intro {
  margin-bottom: var(--space-4);
}

.demo-block,
.artifact-block {
  display: grid;
  gap: var(--space-3);
}

.simple-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: var(--space-2);
  color: var(--color-text);
}

.stack-list {
  display: grid;
  gap: var(--space-3);
}
.stack-item {
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  border: 1px solid var(--color-border-light);
  transition: all var(--transition-fast);
}
.stack-item:hover {
  border-color: var(--color-primary-subtle);
  background: var(--color-surface);
}
.stack-top,
.stack-foot {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.stack-item p,
.stack-foot {
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.error-inline,
.error-text {
  color: var(--color-danger);
}

.detail-block + .detail-block {
  margin-top: var(--space-4);
}
.detail-block {
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  padding: var(--space-3);
}

.agent-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}

@media (max-width: 1024px) {
  .page-header,
  .input-actions,
  .summary-grid,
  .overview-strip,
  .agent-command-bar,
  .command-grid,
  .demo-grid,
  .artifact-summary,
  .artifact-grid {
    grid-template-columns: 1fr;
    display: grid;
  }
  .expert-role-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .directory-heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .summary-metrics {
    grid-template-columns: 1fr;
  }
  .supervisor-summary {
    grid-template-columns: 1fr;
  }
  .action-row {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}

@media (max-width: 640px) {
  .expert-directory {
    padding: var(--space-4);
  }
  .expert-role-grid {
    grid-template-columns: 1fr;
  }
  .expert-role {
    min-height: 0;
  }
}
</style>
