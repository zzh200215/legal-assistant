<template>
  <div class="execution-view">
    <div v-if="runResult" class="summary-grid">
      <!-- 执行结果：卡片外壳统一走 .app-card，读数改 .app-stat，长文本读数用 .is-note 收字号 -->
      <section class="app-card summary-card">
        <div class="app-card-head">
          <h2>执行结果</h2>
          <span class="card-head-tail"><StatusTag kind="agent" :status="runResult.status" /></span>
        </div>
        <div class="app-card-body">
          <div class="app-evidence run-hero">
            <span class="metric-label">执行目标</span>
            <strong class="run-hero-goal">{{ runResult.goal || goal || '未记录目标' }}</strong>
            <p class="body-text">{{ finalAnswer || runResult.error || '等待执行结果返回。' }}</p>
          </div>
          <div class="app-panel-grid-3 summary-metrics">
            <div class="app-stat">
              <span class="app-stat-label">运行 ID</span>
              <div class="app-stat-value is-note">{{ runResult.id || runResult.run_id }}</div>
            </div>
            <div class="app-stat">
              <span class="app-stat-label">总步数</span>
              <div class="app-stat-value">{{ runResult.total_steps || logs.length || 0 }}</div>
            </div>
            <div class="app-stat">
              <span class="app-stat-label">失败原因</span>
              <div class="app-stat-value is-note">{{ runResult.failure_reason || '无' }}</div>
            </div>
          </div>
          <div v-if="finalAnswer" class="app-evidence answer-panel">
            <span class="detail-label">最终答复</span>
            <p class="body-text">{{ finalAnswer }}</p>
          </div>
          <div v-if="runResult.error" class="app-evidence error-panel">
            <span class="detail-label">系统错误</span>
            <p class="body-text">{{ runResult.error }}</p>
          </div>
          <div v-if="['running', 'cancelling', 'awaiting_approval'].includes(runResult.status)" class="card-actions">
            <el-button type="danger" plain :loading="cancelling" @click="cancelCurrentRun">取消执行</el-button>
          </div>
        </div>
      </section>

      <!-- 步骤时间线：复用共享 .app-timeline 骨架与圆点，步骤号移入标题 -->
      <section v-if="logs.length" class="app-card summary-card">
        <div class="app-card-head">
          <h2>步骤时间线</h2>
          <span class="app-card-head-meta">{{ logs.length }} 步</span>
        </div>
        <div class="app-card-body">
          <ol class="app-timeline">
            <li
              v-for="log in logs"
              :key="log.id"
              class="app-timeline-item"
              :class="timelineTone(log.status)"
            >
              <span class="app-timeline-dot" aria-hidden="true"></span>
              <div class="timeline-item-body">
                <div class="timeline-top">
                  <strong>Step {{ log.step }} · {{ toolLabel(log.tool_name) }}</strong>
                  <span class="timeline-meta">{{ actionTypeText(log.action_type) }} · {{ log.duration_ms || 0 }}ms</span>
                </div>
                <div class="timeline-status">
                  <span class="app-tag" :class="statusTone(log.status)">{{ stepStatusLabel(log.status) }}</span>
                  <span v-if="log.error" class="error-inline">{{ log.error }}</span>
                </div>
              </div>
            </li>
          </ol>
        </div>
      </section>
    </div>

    <!-- Worker 编排：去掉旧的主色顶边，头部的状态改为 .app-tag -->
    <section v-if="supervisorPlan.workers?.length" class="app-card supervisor-plan-card">
      <div class="app-card-head">
        <h2>Worker 编排</h2>
        <span class="card-head-tail">
          <span class="app-tag" :class="supervisorPlan.plan_source === 'llm' ? 'is-ok' : 'is-pending'">
            {{ supervisorPlan.plan_source === 'llm' ? '模型计划' : '规则回退' }}
          </span>
          <span v-if="supervisorPlan.execution_mode === 'parallel_read_only'" class="app-tag is-info">只读并行</span>
        </span>
      </div>
      <div class="app-card-body">
        <div class="app-panel-grid-3 supervisor-summary">
          <div class="app-stat">
            <span class="app-stat-label">意图</span>
            <div class="app-stat-value is-note">{{ supervisorPlan.intent || '未识别' }}</div>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">风险等级</span>
            <div class="app-stat-value">
              <span class="app-tag" :class="statusTone(supervisorPlan.risk_level === 'high' ? 'error' : supervisorPlan.risk_level === 'medium' ? 'pending' : 'success')">
                {{ supervisorPlan.risk_level || 'low' }}
              </span>
            </div>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">预期产物</span>
            <div class="app-stat-value is-note">{{ (supervisorPlan.expected_artifacts || []).join(' · ') || '无' }}</div>
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
          <div v-for="(handoff, index) in supervisorPlan.handoffs" :key="`handoff-${index}`" class="app-evidence handoff-row">
            <div class="app-evidence-head">
              <span class="app-evidence-title">{{ workerLabel(handoff.from_worker) }}</span>
              <span class="worker-route-arrow" aria-hidden="true">-></span>
              <span class="app-evidence-title">{{ workerLabel(handoff.to_worker) }}</span>
            </div>
            <span class="app-evidence-text">{{ handoff.completion_summary }}</span>
          </div>
        </div>
        <div v-if="supervisorPlan.aggregation" class="app-evidence aggregation-panel">
          <strong class="aggregation-conclusion">{{ supervisorPlan.aggregation.conclusion }}</strong>
          <ul v-if="supervisorPlan.aggregation.findings?.length" class="simple-list">
            <li v-for="(item, index) in supervisorPlan.aggregation.findings" :key="`aggregation-${index}`">{{ item.title }}<span v-if="item.evidence">：{{ item.evidence }}</span></li>
          </ul>
        </div>
        <div v-if="supervisorPlan.execution_mode === 'parallel_read_only'" class="card-note">仅文档等白名单只读能力可并发执行；写入、草稿和敏感查询仍按审批串行执行。</div>
        <div v-if="supervisorPlan.fallback_reason" class="card-note">回退原因：{{ supervisorPlan.fallback_reason }}</div>
      </div>
    </section>

    <!-- 本次产出对象：计数改 .app-stat，单条产出物改 .app-evidence 行 -->
    <section v-if="hasArtifacts" class="app-card">
      <div class="app-card-head">
        <h2>本次产出对象</h2>
        <span class="card-head-tail"><span class="app-tag is-ok">执行闭环</span></span>
      </div>
      <div class="app-card-body">
        <div class="app-panel-grid-2">
          <div class="app-stat">
            <span class="app-stat-label">文档</span>
            <div class="app-stat-value">{{ artifactGroups.documents.length }}</div>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">任务</span>
            <div class="app-stat-value">{{ artifactGroups.tasks.length }}</div>
          </div>
        </div>
        <div class="artifact-grid">
          <div v-if="artifactGroups.documents.length" class="artifact-block">
            <h3 class="app-card-section-title">文档结果</h3>
            <div v-for="item in artifactGroups.documents" :key="`doc-${item.document_id}`" class="app-evidence">
              <div class="app-evidence-head">
                <strong class="app-evidence-title">文档 {{ item.document_id }}</strong>
                <span class="app-tag">{{ toolLabel(item.tool_name) }}</span>
              </div>
              <p v-if="item.summary" class="app-evidence-text">{{ item.summary }}</p>
              <div class="stack-foot">
                <span v-if="item.risk_count">风险数：{{ item.risk_count }}</span>
                <span v-if="item.chunk_count">命中片段：{{ item.chunk_count }}</span>
              </div>
              <el-button v-if="item.document_id" text type="primary" @click="openDocument(item.document_id)">查看文档</el-button>
            </div>
          </div>

          <div v-if="artifactGroups.tasks.length" class="artifact-block">
            <h3 class="app-card-section-title">创建任务</h3>
            <div v-for="item in artifactGroups.tasks" :key="`task-${item.task_id}`" class="app-evidence">
              <div class="app-evidence-head">
                <strong class="app-evidence-title">{{ item.title || `任务 ${item.task_id}` }}</strong>
                <span class="app-tag is-info">{{ item.status || 'todo' }}</span>
              </div>
              <div class="stack-foot">
                <span v-if="item.assignee">负责人：{{ item.assignee }}</span>
                <span v-if="item.priority">优先级：{{ item.priority }}</span>
              </div>
              <el-button v-if="item.task_id" text type="primary" @click="openTask(item.task_id)">查看任务</el-button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 执行详情：折叠面板保持 Element Plus 组件与交互，只换外壳与间距 -->
    <section v-if="logs.length" class="app-card">
      <div class="app-card-head">
        <h2>执行详情</h2>
        <span class="card-head-tail"><span class="app-tag">{{ logs.length }} 步</span></span>
      </div>
      <div class="app-card-body">
        <div class="app-section-intro compact-intro">
          <strong>步骤级执行回放</strong>
          <span>展开可查看每一步的决策思路、工具参数、执行观察和原始决策载荷。</span>
        </div>
        <el-collapse>
          <el-collapse-item v-for="log in logs" :key="log.id" :name="log.id">
            <template #title>
              <div class="collapse-title">
                <span class="app-tag" :class="statusTone(log.status)">Step {{ log.step }}</span>
                <strong>{{ toolLabel(log.tool_name) }}</strong>
                <span class="timeline-meta">{{ actionTypeText(log.action_type) }}</span>
              </div>
            </template>

            <div v-if="log.thought" class="app-evidence">
              <span class="detail-label">决策思路</span>
              <p class="body-text">{{ log.thought }}</p>
            </div>

            <div v-if="log.error" class="app-evidence">
              <span class="detail-label">失败原因</span>
              <p class="body-text error-text">{{ log.error }}</p>
            </div>

            <div class="app-evidence">
              <span class="detail-label">输入参数</span>
              <el-input type="textarea" :rows="4" :model-value="formatJson(log.input_params)" readonly />
            </div>

            <div v-if="log.observation" class="app-evidence">
              <span class="detail-label">执行观察</span>
              <el-input type="textarea" :rows="5" :model-value="formatJson(log.observation)" readonly />
            </div>

            <div v-if="log.raw_decision" class="app-evidence">
              <span class="detail-label">原始决策</span>
              <el-input type="textarea" :rows="5" :model-value="formatJson(log.raw_decision)" readonly />
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCollapse, ElCollapseItem } from 'element-plus/es/components/collapse/index'
import { ElInput } from 'element-plus/es/components/input/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/collapse/style/css'
import 'element-plus/es/components/collapse-item/style/css'
import 'element-plus/es/components/input/style/css'
// StatusTag 内部渲染 el-tag，标签样式需要在用到它的组件里显式引入
import 'element-plus/es/components/tag/style/css'
import StatusTag from '../StatusTag.vue'
import { actionTypeText, formatJson, toolLabel, workerLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  runResult, logs, finalAnswer, goal, cancelling,
  supervisorPlan, hasArtifacts, artifactGroups,
  cancelCurrentRun, openDocument, openTask,
} = useAgentWorkbench()

/** 步骤状态 → 时间线圆点语气：成功走共享 is-done，执行中用主色，其余按失败处理 */
function timelineTone(status) {
  if (status === 'success') return 'is-done'
  if (status === 'pending') return 'is-running'
  return 'is-error'
}

/** 状态/风险等级 → .app-tag 变体：成功与低风险为绿，执行中与中风险为黄，其余为红 */
function statusTone(status) {
  if (status === 'success') return 'is-ok'
  if (status === 'pending') return 'is-pending'
  return 'is-danger'
}

/** 步骤状态文案，与改造前保持一致 */
function stepStatusLabel(status) {
  if (status === 'success') return '成功'
  if (status === 'pending') return '执行中'
  return '失败'
}
</script>

<style scoped>
/* 本组件的四张卡片之间保持方向 B 的分块间距 */
.execution-view {
  display: grid;
  gap: var(--space-6);
}

/* 卡片头部右侧的状态位：把所有右侧元素成组推到末尾 */
.card-head-tail {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.summary-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: var(--space-6);
}

.run-hero {
  margin-bottom: var(--space-4);
}

.run-hero-goal {
  color: var(--color-text);
  font-size: var(--text-xl);
  line-height: 1.5;
}

.metric-label {
  display: block;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.summary-metrics {
  margin-bottom: var(--space-4);
}

/* 长文本读数（运行 ID、失败原因、意图）用正文档字号，避免撑破数据卡 */
.app-stat-value.is-note {
  font-size: var(--text-sm);
  font-weight: 500;
  line-height: 1.6;
  word-break: break-word;
}

.answer-panel {
  background: var(--color-success-light);
}

.error-panel {
  background: var(--color-danger-light);
}

.answer-panel,
.error-panel,
.aggregation-panel {
  margin-top: var(--space-4);
}

.body-text {
  margin: 0;
  white-space: pre-wrap;
  color: var(--color-text);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.detail-label {
  color: var(--color-text);
  font-size: var(--text-xs);
  font-weight: 600;
}

.card-actions {
  margin-top: var(--space-4);
}

.timeline-item-body {
  display: grid;
  gap: var(--space-2);
  align-content: start;
}

.timeline-top,
.timeline-status,
.collapse-title {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}

/* 失败步骤的圆点转红，执行中保持主色，成功沿用共享 is-done 的强调色 */
.app-timeline-item.is-error .app-timeline-dot {
  border-color: var(--color-danger);
  background: var(--color-danger);
}

.app-timeline-item.is-running .app-timeline-dot {
  border-color: var(--color-accent);
  background: var(--color-accent-light);
}

.timeline-meta {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

.supervisor-summary {
  margin-bottom: var(--space-4);
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
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.worker-node > span {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  color: var(--color-text-inverse);
  background: var(--color-primary);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: 700;
}

.worker-route-arrow {
  flex: 0 0 auto;
  color: var(--color-text-muted);
  font-family: var(--font-mono);
}

.handoff-list {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.handoff-row .app-evidence-head {
  justify-content: flex-start;
}

.aggregation-conclusion {
  color: var(--color-text);
  font-size: var(--text-sm);
}

.card-note {
  margin-top: var(--space-3);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}

.compact-intro {
  margin-bottom: var(--space-4);
}

.artifact-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-5);
}

.artifact-block {
  display: grid;
  gap: var(--space-3);
  align-content: start;
}

.simple-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: var(--space-2);
  color: var(--color-text);
}

.stack-foot {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.error-inline,
.error-text {
  color: var(--color-danger);
}

/* 折叠面板内的证据行之间留出间距 */
.app-evidence + .app-evidence {
  margin-top: var(--space-3);
}

@media (max-width: 1024px) {
  .summary-grid,
  .artifact-grid {
    grid-template-columns: 1fr;
  }
}
</style>
