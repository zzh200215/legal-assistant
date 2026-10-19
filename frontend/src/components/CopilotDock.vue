<template>
  <aside class="copilot-dock" :class="{ collapsed }" aria-label="法律助手">
    <header class="cop-head">
      <span class="cop-spark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">
          <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
        </svg>
      </span>
      <div v-if="!collapsed" class="cop-head-text">
        <b>法律助手</b>
        <span>懂你当前在看的案件</span>
      </div>
      <button
        v-if="!collapsed"
        class="cop-fullpage"
        type="button"
        title="在整页中打开案件助手"
        aria-label="在整页中打开案件助手"
        @click="openFullPage"
      >
        整页
      </button>
      <button
        class="cop-toggle"
        type="button"
        :title="collapsed ? '展开法律助手' : '收起法律助手'"
        :aria-label="collapsed ? '展开法律助手' : '收起法律助手'"
        :aria-expanded="collapsed ? 'false' : 'true'"
        @click="emit('toggle')"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path :d="collapsed ? 'M15 18l-6-6 6-6' : 'M9 18l6-6-6-6'" />
        </svg>
      </button>
    </header>

    <template v-if="!collapsed">
      <div class="cop-body">
        <!-- 上下文条：模块名与案件都只取自路由（案件只显示 id，不额外请求案件详情） -->
        <div class="ctx">
          <svg class="ctx-icon" aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 6L9 17l-5-5" />
          </svg>
          <span class="ctx-label">当前：</span>
          <b>{{ context.moduleLabel }}</b>
          <template v-if="context.hasCase">
            <span class="ctx-sep" aria-hidden="true">·</span>
            <b>{{ context.caseLabel }}</b>
          </template>
          <template v-if="context.hasItem">
            <span class="ctx-sep" aria-hidden="true">·</span>
            <b>{{ context.itemLabel }}</b>
          </template>
        </div>

        <p v-if="questionBubble" class="cop-question">{{ questionBubble }}</p>

        <!-- 离线：明确网络态（QueryStateView 的 offline 文案），下方若已有答复则照旧展示 -->
        <QueryStateView v-if="viewState === 'offline'" status="offline" min-height="0px" />

        <!-- 生成中：一次同步长请求（LLM 20-30s），只给时间维度的阶段提示，不伪造步骤/耗时/命中结果 -->
        <div v-else-if="viewState === 'loading'" class="cop-progress-block">
          <div class="cop-progress" role="status" aria-live="polite">
            <span class="cop-progress-dot" aria-hidden="true"></span>
            <span>{{ stageText }}</span>
          </div>
          <!-- 口径如实交代：这条链路拿不到实时步骤事件，别让阶段文案被读成服务端进度 -->
          <p class="cop-progress-note">该接口一次请求返回全部结果，没有实时步骤事件；答复回来后再展示服务端记录的处理过程。</p>
        </div>

        <!-- 失败：统一错误态 + 可重试；文案由 QueryStateView 内部的 normalizeError 映射 -->
        <QueryStateView v-else-if="viewState === 'error'" status="error" :error="consultError" min-height="0px" @retry="runConsultation" />

        <!-- 结果区（懒加载 chunk）：字段全部来自服务端响应，缺字段就不渲染，不用本地假值补齐 -->
        <CopilotAnswer
          v-if="visibleResult"
          :result="visibleResult"
          :faithfulness-state="faithfulnessState"
          @feedback-submitted="onFeedbackSubmitted"
        />

        <!-- 空态：未提问。没有案件上下文时说明清楚，但仍允许提问 -->
        <QueryStateView v-if="viewState === 'empty'" status="empty" min-height="0px">
          <template #empty>
            <div class="cop-empty">
              <strong>{{ context.hasCase ? '尚未提问' : '尚未绑定案件' }}</strong>
              <span v-if="context.hasCase">提问会带上 {{ context.caseLabel }} 作为上下文。</span>
              <span v-else>仍可直接提问，但答复不会关联到具体案件。</span>
            </div>
          </template>
        </QueryStateView>
      </div>

      <form class="cop-input" @submit.prevent="runConsultation">
        <div class="cop-input-box">
          <svg class="cop-input-icon" aria-hidden="true" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">
            <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
          </svg>
          <textarea
            v-model="consultForm.question"
            class="cop-textarea"
            rows="2"
            maxlength="12000"
            aria-label="法律问题"
            placeholder="基于当前案件继续问…"
            :disabled="consultLoading || !online"
            @keydown.enter.exact.prevent="runConsultation"
          ></textarea>
          <button class="cop-send" type="submit" :disabled="!canSubmit" :title="online ? '提问（Enter 提交）' : '离线状态无法提交'">
            {{ consultLoading ? '生成中…' : '提问' }}
          </button>
        </div>
      </form>
    </template>
  </aside>
</template>

<script setup>
import { computed, defineAsyncComponent, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import legalWorkspace from '../api/legalWorkspace'
import { useCopilotContext, copilotContextPayload } from '../composables/useCopilotContext'
import { useLegalConsultations } from '../composables/useLegalConsultations'
import { useStagedProgress } from '../composables/useStagedProgress'
import { useOnline } from '../utils/network.js'
import QueryStateView from './QueryStateView.vue'

// copilot 外壳 PoC：在「法律咨询」上下文接通一次真实问答，并给出明确降级态。
// 数据来源只有一处：POST /legal/consultations 的响应（领域逻辑复用 useLegalConsultations）。
// 本组件内不存在任何样例答案/兜底假数据——没数据就渲染空态/错误态，失败就明说失败。
// 答复渲染块按需加载，避免把表单类 element-plus 组件压进入口 bundle（CI 有入口体积门禁）。
const CopilotAnswer = defineAsyncComponent(() => import('./copilot/CopilotAnswer.vue'))

defineProps({ collapsed: { type: Boolean, default: false } })
const emit = defineEmits(['toggle'])

// 失败由面板内的 QueryStateView 错误态承载（含重试），不再叠加全局 toast；
// 空输入在提交前已拦截，warning/success 通道不会被调用。
// useLegalConsultations 的 onError 只把文案交给 message，错误对象不外传，故这里用不到它。
const INLINE_MESSAGES = { error() {}, warning() {}, success() {} }

const online = useOnline()
const context = useCopilotContext()
const router = useRouter()

// 「整页」= 助手入口收敛后保留的深度视图：面板是唯一入口，整页视图从这里进入。
// 案件上下文随 URL 带过去，整页里的会话仍绑定同一个案件。
const openFullPage = () => {
  const caseId = context.value.caseId
  router.push(caseId ? { path: '/chat', query: { case_id: String(caseId) } } : { path: '/chat' })
}

// 面板唯一对领域模块的包装：转发请求并留下原始错误对象，
// 供 QueryStateView 内部用 normalizeError 生成文案（幂等键/解包/case_id 传参仍由 api 层负责）。
const lastRequestError = shallowRef(null)
const captureRequestError = (error) => {
  lastRequestError.value = error
}
const consultClient = {
  ...legalWorkspace,
  async createLegalConsultation(payload, options) {
    try {
      return await legalWorkspace.createLegalConsultation(payload, options)
    } catch (error) {
      captureRequestError(error)
      throw error
    }
  },
}

const { consultForm, consultLoading, consultResult, submitConsultation } = useLegalConsultations({
  client: consultClient,
  message: INLINE_MESSAGES,
  caseId: computed(() => context.value.caseId),
  // 用户在「合同审查 / 文书」tab 选中某一份时，把它的 id 一并交给服务端：
  // 后端按 id 取正文并做权限校验（客户端不传正文，也就没有伪造上下文的可能）。
  context: computed(() => copilotContextPayload(context.value)),
  // 面板常驻外壳、只做「问一次」：不读历史列表，也就不该为它发请求
  withHistory: false,
})

const consultError = shallowRef(null)
const lastQuestion = ref('')

// 忠实度校验是「答案之后」的独立动作：判官是一次完整 LLM 往返（真机约 10s），
// 塞进创建咨询的请求里会让「提问→答案」多等一个数量级。答案先渲染，这里再补判并翻转标记。
// 判官不可用/网络失败时静默保持「未校验」——面板已有该状态，不额外打扰用户。
const faithfulnessState = ref('idle')
const runFaithfulnessCheck = async (consultationId) => {
  if (!consultationId || !online.value) return
  faithfulnessState.value = 'checking'
  try {
    const response = await consultClient.checkConsultationFaithfulness(consultationId)
    const verdict = response?.data
    // 只有真拿到结论才覆盖：判官不可用的空结论不该抹掉响应里已有的判断（例如已存的历史结论）。
    if (verdict?.available && consultResult.value?.id === consultationId) {
      consultResult.value = { ...consultResult.value, faithfulness: verdict }
    }
  } catch {
    // 判官不可用：不落库、不改口径，徽标留在「未校验」
  } finally {
    faithfulnessState.value = 'idle'
  }
}

const runConsultation = async () => {
  const question = consultForm.value.question.trim()
  if (!question || consultLoading.value || !online.value) return
  consultForm.value.question = question
  lastQuestion.value = question
  consultError.value = null
  lastRequestError.value = null
  await submitConsultation()
  // 失败时 useMutation 会调 onError 并吞掉异常（不向外抛），因此失败与否以「原始错误」为准。
  // 传原始错误对象而非已规范化结果：normalizeError 对自己的返回值不可重入
  // （api/errors.js 的 __normalized 守卫只认对象），预先规范化再传进去会退化成通用文案。
  if (lastRequestError.value) {
    consultError.value = lastRequestError.value
    lastRequestError.value = null
    return
  }
  // 不 await：忠实度是补判，答复已经可读，不该让用户等判官。
  void runFaithfulnessCheck(consultResult.value?.id)
}

const viewState = computed(() => {
  if (!online.value) return 'offline'
  if (consultLoading.value) return 'loading'
  if (consultError.value) return 'error'
  return consultResult.value ? 'result' : 'empty'
})

// 离线时展示最后一次真实答复（不得伪造）；失败/生成中不展示上一条答复，避免与新问题错配
const visibleResult = computed(() => {
  if (viewState.value === 'result' || viewState.value === 'offline') return consultResult.value
  return null
})

const questionBubble = computed(() => {
  if (visibleResult.value) return visibleResult.value.question || ''
  if (viewState.value === 'loading' || viewState.value === 'error') return lastQuestion.value
  return ''
})

const canSubmit = computed(() => online.value && !consultLoading.value && Boolean(consultForm.value.question.trim()))

// 阶段文案与 LegalConsultationsTab 保持一致；阶段按时间轮换，不代表服务端真实步骤
const stageText = useStagedProgress(consultLoading, [
  '正在检索相关法源…',
  '正在分析问题要点与风险…',
  '正在生成法律建议…',
])

const onFeedbackSubmitted = (score) => {
  if (consultResult.value) consultResult.value.feedback_score = score
}
</script>

<style scoped>
/* 版式基准：docs/review/ui-preview-direction-b.html 的右侧 copilot 面板（.cop-head / .ctx /
   .cop-body / .cop-input）。层次靠 1px 中性边框与浅底表达，颜色一律走 design-tokens。 */
.copilot-dock {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  position: sticky;
  top: 0;
  overflow: hidden;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
}

.cop-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
}

.cop-spark {
  display: grid;
  place-items: center;
  flex: none;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: var(--color-text-inverse);
}

.cop-head-text {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.cop-head-text b {
  color: var(--color-text);
  font-size: var(--text-base);
  font-weight: 700;
}

.cop-head-text span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.cop-toggle {
  display: grid;
  place-items: center;
  flex: none;
  width: 30px;
  height: 30px;
  margin-left: auto;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

/* 「整页」入口：入口收敛后面板是唯一入口，深度视图由这里进入（紧贴收起按钮左侧） */
.cop-fullpage {
  flex: none;
  margin-left: auto;
  padding: 0 var(--space-2);
  height: 30px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast), border-color var(--transition-fast);
}

.cop-fullpage:hover {
  background: var(--color-surface-hover);
  border-color: var(--color-border-hover);
  color: var(--color-text);
}

.cop-fullpage:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 1px;
}

.cop-fullpage + .cop-toggle {
  margin-left: var(--space-1);
}

.cop-toggle:hover {
  background: var(--color-bg-alt);
  color: var(--color-text);
}

.cop-toggle:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 1px;
}

/* 收起态（栅格列宽 52px）：只留品牌图标 + 展开按钮 */
.copilot-dock.collapsed .cop-head {
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2);
}

.copilot-dock.collapsed .cop-toggle {
  margin-left: 0;
}

.cop-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  flex: 1;
  min-height: 0;
  padding: var(--space-4);
  overflow: auto;
}

/* 上下文条：浅底 + 1px 边框的胶囊，只说「当前在看什么」，不做主色强调 */
.ctx {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 11px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.ctx-icon {
  flex: none;
  color: var(--color-accent);
}

.ctx b {
  color: var(--color-text);
  font-weight: 600;
}

.ctx-sep {
  color: var(--color-text-muted);
}

/* 提问原文：贴左的气泡（右下角收窄），主色只做浅底 + 深字 */
.cop-question {
  align-self: flex-start;
  margin: 0;
  max-width: 100%;
  padding: 10px 13px;
  border-radius: var(--radius-md) var(--radius-md) var(--radius-md) var(--radius-xs);
  background: var(--color-primary-light);
  color: var(--color-primary-active);
  font-size: var(--text-sm);
  font-weight: 600;
  line-height: 1.6;
  word-break: break-word;
}

/* 生成中：与过程时间线的「进行中」节点同形（品牌色圆点 + 光圈），不冒充服务端进度条 */
.cop-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-weight: 600;
}

.cop-progress-dot {
  flex: none;
  width: 10px;
  height: 10px;
  border-radius: var(--radius-full);
  background: var(--color-surface);
  border: 2px solid var(--color-primary);
  box-shadow: 0 0 0 4px var(--color-primary-subtle);
  animation: cop-pulse 1.2s ease-in-out infinite;
}

@keyframes cop-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.cop-progress-block {
  display: grid;
  gap: var(--space-2);
}

.cop-progress-note {
  margin: 0;
  padding: 0 var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}

.cop-empty {
  display: grid;
  gap: var(--space-1);
}

.cop-empty strong {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}

.cop-empty span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

/* 输入区：预览里的单行胶囊输入框 + 发送键；这里是多行 textarea，
   所以用同一套「浅底 + 1px 边框 + 大圆角 + 左侧图标」的容器包住它。 */
.cop-input {
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-light);
}

.cop-input-box {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  min-width: 0;
  padding: 6px 6px 6px 11px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  background: var(--color-bg);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.cop-input-box:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-subtle);
}

.cop-input-icon {
  flex: none;
  align-self: flex-start;
  margin-top: 9px;
  color: var(--color-text-muted);
}

.cop-textarea {
  flex: 1;
  min-width: 0;
  margin: 5px 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  font-size: var(--text-sm);
  line-height: 1.6;
  resize: none;
}

.cop-textarea:focus {
  outline: none;
}

.cop-textarea::placeholder {
  color: var(--color-text-muted);
}

.cop-textarea:disabled {
  color: var(--color-text-muted);
}

.cop-send {
  flex: none;
  padding: 7px var(--space-3);
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-full);
  background: var(--color-primary);
  color: var(--color-text-inverse);
  font: inherit;
  font-size: var(--text-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast);
}

.cop-send:hover:not(:disabled) {
  background: var(--color-primary-hover);
  border-color: var(--color-primary-hover);
}

.cop-send:disabled {
  border-color: var(--color-border);
  background: var(--color-bg-alt);
  color: var(--color-text-muted);
  cursor: not-allowed;
}

/* 窄屏（≤1024px）：面板不再占栅格列（App.vue 同一断点已把第三列去掉），改为右侧浮层，
   避免固定预留给正文的宽度被压缩；收起态只留右上角一个 52px 的展开条。 */
@media (max-width: 1024px) {
  .copilot-dock {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    left: auto;
    width: var(--copilot-w, 380px);
    max-width: 100vw;
    height: auto;
    z-index: var(--z-sidebar, 100);
    box-shadow: var(--shadow-lg);
  }

  .copilot-dock.collapsed {
    bottom: auto;
    width: 52px;
    height: auto;
    box-shadow: var(--shadow-sm);
  }
}

@media (max-width: 760px) {
  /* 移动端：顶栏与底部导航都固定 56px（App.vue，z-index 高于本面板），
     浮层夹在两者之间，收起后的展开条也不会被顶栏盖住点不到 */
  .copilot-dock {
    top: 56px;
    bottom: 56px;
  }
}
</style>
