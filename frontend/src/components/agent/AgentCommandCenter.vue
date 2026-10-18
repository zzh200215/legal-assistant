<template>
  <div class="command-center">
    <div class="command-grid">
      <!-- 一句话目标：外壳换成方向 B 的 .app-card；.input-card 是 agent-flow e2e 的 textarea 锚点，类名必须保留 -->
      <section class="app-card input-card">
        <div class="app-card-head">
          <h2>一句话目标</h2>
          <span class="app-card-head-meta">低风险步骤直接执行，敏感动作执行到该步再确认</span>
        </div>
        <div class="app-card-body">
          <el-input
            v-model="goal"
            type="textarea"
            :rows="4"
            placeholder="例如：总结文档 1，并提取风险点；再生成一份审查意见"
          />
          <div class="input-actions">
            <p class="input-note">文档分析、检索和草稿生成会直接继续；创建任务、批量落待办及敏感数据查询会在执行到该步骤时请求确认。</p>
            <div class="action-row">
              <span v-if="demoContext.documentId" class="app-tag is-pending demo-tag">
                演示文档 {{ demoContext.documentId }}{{ demoContext.documentTitle ? ` · ${demoContext.documentTitle}` : '' }}
              </span>
              <span class="meta-text">最大步数</span>
              <el-input-number v-model="maxSteps" :min="2" :max="10" size="small" />
              <el-button :loading="previewLoading" @click="previewPlan">预览计划</el-button>
              <button type="button" class="workspace-primary is-compact" :disabled="loading" @click="run">直接执行</button>
            </div>
          </div>
        </div>
      </section>

      <!-- 演示链路：说明性内容收进 .app-card，不再用 el-card 的 header 插槽 -->
      <section v-if="demoPreset" class="app-card demo-card">
        <div class="app-card-head">
          <h2>{{ demoPreset.title }}</h2>
          <span class="app-tag is-pending card-head-action">标准演示链路</span>
        </div>
        <div class="app-card-body">
          <div class="demo-grid">
            <div class="demo-block">
              <span class="app-card-section-title">演示说明</span>
              <p class="body-text">{{ demoPreset.description }}</p>
            </div>
            <div class="demo-block">
              <span class="app-card-section-title">当前文档</span>
              <p class="body-text">
                {{ demoPreset.documentId ? `文档 ${demoPreset.documentId}` : '未指定文档' }}
                <span v-if="demoPreset.documentTitle"> · {{ demoPreset.documentTitle }}</span>
              </p>
            </div>
            <div class="demo-block">
              <span class="app-card-section-title">预期工具路径</span>
              <ul class="simple-list">
                <li v-for="(item, index) in demoPreset.expectedFlow" :key="`demo-flow-${index}`">{{ item }}</li>
              </ul>
            </div>
            <div class="demo-block">
              <span class="app-card-section-title">演示检查点</span>
              <ul class="simple-list">
                <li v-for="(item, index) in demoPreset.successCriteria" :key="`demo-success-${index}`">{{ item }}</li>
              </ul>
            </div>
          </div>
          <div class="app-card-section">
            <span class="app-card-section-title">执行前建议</span>
            <ul class="simple-list">
              <li v-for="(item, index) in demoPreset.prepChecklist" :key="`demo-prep-${index}`">{{ item }}</li>
            </ul>
          </div>
        </div>
      </section>
    </div>

    <!-- 执行计划预览：读数改 .app-stat，步进条目复用共享时间线骨架 -->
    <section v-if="planPreview" class="app-card preview-card">
      <div class="app-card-head">
        <h2>执行计划预览</h2>
        <span class="app-tag card-head-action" :class="planPreview.can_execute ? 'is-ok' : 'is-pending'">
          {{ planPreview.can_execute ? '可执行' : '需补充信息' }}
        </span>
      </div>
      <div class="app-card-body">
        <div>
          <span class="app-card-section-title">计划概述</span>
          <p class="body-text">{{ planPreview.summary }}</p>
        </div>

        <div class="app-panel-grid-2 preview-metrics">
          <div class="app-stat">
            <span class="app-stat-label">预计步数</span>
            <div class="app-stat-value">{{ planPreview.estimated_steps }}</div>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">涉及工具</span>
            <div class="app-stat-value">{{ planPreview.steps.length }}</div>
          </div>
        </div>

        <ol class="app-timeline preview-timeline">
          <li v-for="step in planPreview.steps" :key="`preview-${step.step}`" class="app-timeline-item">
            <span class="app-timeline-dot" aria-hidden="true"></span>
            <div class="timeline-item-body">
              <strong class="app-evidence-title">Step {{ step.step }} · {{ toolLabel(step.tool_name) }}</strong>
              <p class="body-text">{{ step.purpose }}</p>
              <div v-if="Object.keys(step.action_input_preview || {}).length" class="app-evidence timeline-params">
                <span class="detail-label">预估参数</span>
                <el-input type="textarea" :rows="3" :model-value="formatJson(step.action_input_preview)" readonly />
              </div>
            </div>
          </li>
        </ol>

        <div v-if="planPreview.risks?.length" class="app-card-section">
          <span class="app-card-section-title">风险与缺口</span>
          <ul class="simple-list">
            <li v-for="(risk, index) in planPreview.risks" :key="`risk-${index}`">{{ risk }}</li>
          </ul>
        </div>

        <div class="preview-actions">
          <button
            type="button"
            class="workspace-primary is-compact"
            :disabled="loading || !planPreview.can_execute"
            @click="executeRun"
          >立即执行</button>
        </div>
      </div>
    </section>

    <el-dialog v-model="sensitiveApprovalVisible" title="确认敏感操作" width="560px" class="agent-dialog" append-to-body :close-on-click-modal="false">
      <template v-if="activeApproval">
        <div>
          <span class="app-card-section-title">即将执行</span>
          <p class="body-text">{{ toolLabel(activeApproval.tool_name) }}</p>
        </div>
        <div class="dialog-section">
          <span class="app-card-section-title">操作说明</span>
          <p class="body-text">该步骤会创建内部任务、批量落待办或读取敏感数据。确认后，Agent 将从当前步骤继续执行。</p>
        </div>
        <div class="app-evidence dialog-detail">
          <span class="detail-label">执行参数</span>
          <el-input type="textarea" :rows="5" :model-value="formatJson(activeApproval.input_params)" readonly />
        </div>
      </template>
      <template #footer>
        <el-button :disabled="loading" @click="decideApproval(activeApproval, false)">拒绝操作</el-button>
        <button
          type="button"
          class="workspace-primary is-compact"
          :disabled="loading"
          @click="decideApproval(activeApproval, true)"
        >确认并继续</button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElInputNumber } from 'element-plus/es/components/input-number/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import { formatJson, toolLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  goal, maxSteps, loading, previewLoading, demoContext, demoPreset,
  planPreview, sensitiveApprovalVisible, activeApproval,
  previewPlan, run, executeRun, decideApproval,
} = useAgentWorkbench()
</script>

<style scoped>
.command-center {
  display: grid;
  gap: var(--space-6);
}

/* 输入卡片与预览卡片之间是页内分块，保持方向 B 的分块间距 */
.command-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(320px, 0.9fr);
  gap: var(--space-6);
  align-items: start;
}

/* .input-card 的类名是 e2e 锚点，视觉全部交给 .app-card 共享类 */

.card-head-action {
  margin-left: auto;
}

.input-actions {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3) var(--space-4);
  align-items: center;
  flex-wrap: wrap;
  margin-top: var(--space-4);
}

.input-note {
  margin: 0;
  max-width: 52ch;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.7;
}

.action-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-left: auto;
}

/* 演示文档标签可能带较长的文档标题，允许换行，避免撑破动作行 */
.demo-tag {
  white-space: normal;
}

.meta-text {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}

.preview-metrics {
  margin-top: var(--space-5);
}

.preview-timeline {
  margin-top: var(--space-5);
}

.timeline-item-body {
  display: grid;
  gap: var(--space-2);
  align-content: start;
}

.timeline-params {
  margin-top: var(--space-1);
}

.demo-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.demo-block {
  display: grid;
  gap: var(--space-2);
  align-content: start;
}

/* 演示块内是“小标题 + 正文”的紧凑组合，收紧共享标题类的下外边距 */
.demo-block .app-card-section-title {
  margin: 0;
}

.body-text {
  margin: 0;
  white-space: pre-wrap;
  color: var(--color-text);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.simple-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.preview-actions {
  margin-top: var(--space-5);
}

.dialog-section {
  margin-top: var(--space-5);
}

.dialog-detail {
  margin-top: var(--space-5);
}

.detail-label {
  color: var(--color-text);
  font-size: var(--text-xs);
  font-weight: 600;
}

.agent-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}

@media (max-width: 1024px) {
  .command-grid,
  .demo-grid {
    grid-template-columns: 1fr;
  }

  .input-note {
    max-width: none;
  }

  .action-row {
    justify-content: flex-start;
  }
}
</style>
