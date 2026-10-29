<template>
  <div class="chat-page">
    <aside class="chat-sidebar">
      <div class="chat-sidebar-head">
        <div class="chat-sidebar-title">{{ caseTitle ? '案件助手' : '对话记录' }}</div>
        <div class="chat-sidebar-sub">{{ caseTitle ? '围绕当前案件资料继续工作。' : '进入案件后，助手会自动绑定案件上下文。' }}</div>
      </div>
      <section v-if="caseTitle" class="chat-case">
        <div class="chat-meta-label">当前案件</div>
        <div class="chat-case-title">{{ caseTitle }}</div>
        <div v-if="caseSummary" class="chat-case-counts">
          <span>咨询 <strong>{{ caseSummary.counts?.consultations || 0 }}</strong></span>
          <span>审查 <strong>{{ caseSummary.counts?.contract_reviews || 0 }}</strong></span>
          <span>文书 <strong>{{ caseSummary.counts?.drafts || 0 }}</strong></span>
          <span>待审 <strong>{{ caseSummary.counts?.pending_reviews || 0 }}</strong></span>
        </div>
        <button type="button" class="chat-link-button" @click="router.push({ path: '/legal-workspace', query: { case_id: caseId } })">打开案件</button>
      </section>
      <div class="chat-sidebar-body">
        <button class="chat-session">
          <div class="chat-session-title">当前实时会话</div>
          <div class="chat-session-id">{{ currentSessionId || '连接后自动生成 Session' }}</div>
        </button>
        <div class="chat-message-stats">
          <div class="chat-meta-label">消息统计</div>
          <div class="chat-stat-row">
            <span class="chat-stat-number">{{ messages.length }}</span>
            <span class="chat-meta">条消息</span>
          </div>
        </div>
        <section class="chat-prefs">
          <div class="chat-prefs-head">
            <span class="chat-meta-label">个人偏好</span>
            <button type="button" class="chat-link-button" @click="preferencesOpen = !preferencesOpen">
              {{ preferencesOpen ? '收起' : '管理' }}
            </button>
          </div>
          <div v-if="preferencesOpen" class="chat-prefs-list">
            <div v-for="item in preferences" :key="item.id" class="chat-pref-item">
              <div class="chat-pref-row">
                <div class="chat-pref-text">
                  <div class="chat-pref-key">{{ item.preference_key }}</div>
                  <div class="chat-pref-value">{{ item.preference_value }}</div>
                </div>
                <button type="button" class="chat-pref-delete" @click="removePreference(item.id)">删除</button>
              </div>
            </div>
            <div v-if="!preferences.length" class="chat-meta">尚未保存个人偏好</div>
            <el-input v-model="preferenceKey" size="small" maxlength="128" placeholder="偏好名称，例如：审查风格" />
            <el-input v-model="preferenceValue" size="small" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="偏好内容，例如：结论优先，语气简洁" />
            <el-button size="small" type="primary" :loading="preferenceSaving" @click="savePreference">保存偏好</el-button>
          </div>
        </section>
      </div>
      <div class="chat-status">
        <span class="chat-dot" :class="loading ? 'is-busy' : 'is-ready'"></span>
        <span class="chat-meta">{{ loading ? '正在生成回复' : '实时通道待命' }}</span>
      </div>
    </aside>

    <section class="chat-main">
      <header class="chat-main-head">
        <div>
          <div class="chat-main-title">{{ caseTitle ? '案件助手' : '法律对话' }}</div>
          <div v-if="caseTitle" class="chat-main-sub">当前案件：{{ caseTitle }} · 基于案件上下文开展工作</div>
          <div v-else class="chat-main-sub">进入案件后，助手会自动绑定案件资料。</div>
        </div>
        <div class="chat-main-actions">
          <button v-if="caseId" type="button" class="chat-link-button" @click="router.push({ path: '/legal-workspace', query: { case_id: caseId } })">返回案件</button>
          <span class="chat-connection">
            <span class="chat-dot" :class="connected ? 'is-ready' : 'is-idle'"></span>
            <span class="chat-meta">{{ connected ? '实时通道已连接' : '连接中' }}</span>
          </span>
        </div>
      </header>

      <div class="chat-messages">
        <div v-if="!messages.length" class="chat-empty">
          <div class="chat-empty-icon">
            <el-icon :size="22"><ChatLineRound /></el-icon>
          </div>
          <div class="chat-empty-title">{{ caseTitle ? `开始处理「${caseTitle}」` : '开始一次法律对话' }}</div>
          <p class="chat-empty-text">{{ caseTitle ? '可以分析案情、查找法源、准备文书或梳理下一步工作。最终意见请由律师确认。' : '进入案件后，助手会带入案件上下文，帮助你处理案情、法源和文书。' }}</p>
        </div>

        <div
          v-for="(msg, i) in messages"
          :key="msg.id ?? `msg-${i}`"
          class="chat-message-row"
          :class="{ 'is-user': msg.role === 'user' }"
        >
          <div class="chat-message-stack">
            <div class="chat-message-meta" :class="{ 'is-user': msg.role === 'user' }">
              <span>{{ msg.role === 'user' ? '你' : 'AI 助手' }}</span>
              <span>{{ msg.streaming ? '正在输出' : '已完成' }}</span>
            </div>
            <div class="chat-bubble" :class="msg.role === 'user' ? 'is-user' : 'is-assistant'">
              <p class="chat-bubble-text">{{ msg.content }}<span v-if="msg.streaming" class="cursor-blink">|</span></p>
              <div v-if="msg.streaming && !msg.content" class="typing-dots">
                <span></span><span></span><span></span>
              </div>
            </div>

            <div v-if="msg.citations?.length" class="chat-citations">
              <button v-for="(item, index) in msg.citations" :key="`citation-${i}-${index}`" type="button" class="chat-citation" @click="openCitation(item)">
                <div class="chat-citation-head">
                  <span class="chat-citation-title">引用 {{ index + 1 }}</span>
                  <span v-if="item.page_number || item.document_id" class="chat-citation-page">{{ item.page_number ? `第 ${item.page_number} 页` : '打开资料' }}</span>
                </div>
                <p class="chat-citation-text">{{ item.source_text || item.quote || item.content }}</p>
              </button>
            </div>
            <div v-if="caseId && !msg.streaming && msg.role === 'assistant' && msg.content" class="chat-quick-actions">
              <button type="button" @click="sendQuickAction('根据当前案件资料生成一份待审核的法律咨询摘要')">形成咨询摘要</button>
              <button type="button" @click="sendQuickAction('根据当前案件资料起草一份文书，并列出缺失事实')">准备文书草稿</button>
              <button type="button" @click="sendQuickAction('从当前案件资料提取下一步任务和关键日期')">整理下一步</button>
            </div>
          </div>
        </div>
      </div>

      <footer class="chat-composer">
        <div class="chat-composer-row">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            class="chat-input"
            @keydown.enter.exact.prevent="send"
          />
          <el-button type="primary" :loading="loading" class="send-button" @click="send">发送</el-button>
        </div>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatLineRound } from '@element-plus/icons-vue'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElMessage } from 'element-plus/es/components/message/index'
import memory from '../api/memory'
import legalWorkspace from '../api/legalWorkspace'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'

const messages = ref([])
const input = ref('')
const loading = ref(false)
const connected = ref(false)
const currentSessionId = ref(null)
const preferences = ref([])
const preferencesOpen = ref(false)
const preferenceKey = ref('')
const preferenceValue = ref('')
const preferenceSaving = ref(false)
const route = useRoute()
const router = useRouter()
const caseId = computed(() => route.query.case_id ? Number(route.query.case_id) : null)
const caseTitle = ref('')
const caseSummary = ref(null)
let ws = null
let reconnectTimer = null

const connectWS = () => {
  const token = localStorage.getItem('token')
  if (!token) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const wsHost = import.meta.env.DEV
    ? (import.meta.env.VITE_WS_HOST || 'localhost:8001')
    : location.host
  const wsUrl = `${proto}://${wsHost}/api/ws/chat`
  ws = new WebSocket(wsUrl, ['json', `bearer.${token}`])

  ws.onopen = () => {
    connected.value = true
  }

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data)
    if (data.type === 'session') {
      currentSessionId.value = data.session_id
    } else if (data.type === 'chunk') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming === true) {
        last.content += data.content
      }
    } else if (data.type === 'done') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming === true) {
        last.streaming = false
        if (data.citations) last.citations = data.citations
      }
      loading.value = false
    } else if (data.type === 'error') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming === true) {
        last.streaming = false
      }
      ElMessage.error(data.content)
      loading.value = false
    }
  }

  ws.onerror = (e) => {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last.streaming === true) {
      last.streaming = false
    }
    console.error('WebSocket error:', e)
    ElMessage.error('实时连接失败')
    loading.value = false
  }

  ws.onclose = (e) => {
    connected.value = false
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last.streaming === true) {
      last.streaming = false
    }
    loading.value = false
    if (e.code !== 1000) {
      reconnectTimer = setTimeout(connectWS, 3000)
    }
  }
}

const send = () => {
  if (!input.value.trim()) return
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    ElMessage.error('实时连接尚未建立')
    return
  }

  const ts = Date.now()
  messages.value.push({ id: `u-${ts}`, role: 'user', content: input.value })
  const assistantMsg = { id: `a-${ts}`, role: 'assistant', content: '', streaming: true }
  const assistantIndex = messages.value.push(assistantMsg) - 1

  try {
    ws.send(JSON.stringify({
      content: input.value,
      session_id: currentSessionId.value,
      case_id: caseId.value || undefined,
    }))
  } catch (e) {
    console.error('发送消息失败', e)
    if (messages.value[assistantIndex] && messages.value[assistantIndex].streaming) {
      messages.value[assistantIndex].streaming = false
    }
    loading.value = false
    ElMessage.error('消息发送失败')
    return
  }

  input.value = ''
  loading.value = true
}

const sendQuickAction = (content) => {
  input.value = content
  send()
}

const openCitation = (item) => {
  if (item?.document_id) {
    router.push({ path: '/documents', query: { documentId: String(item.document_id), case_id: String(caseId.value) } })
    return
  }
  router.push({ path: '/legal-workspace', query: { view: 'research', case_id: String(caseId.value) } })
}

const loadPreferences = async () => {
  try {
    const { data } = await memory.listMemoryPreferences()
    preferences.value = data || []
  } catch {
    preferences.value = []
  }
}

const savePreference = async () => {
  if (!preferenceKey.value.trim() || !preferenceValue.value.trim()) {
    ElMessage.warning('请填写偏好名称和内容')
    return
  }
  preferenceSaving.value = true
  try {
    await memory.saveMemoryPreference({
      category: 'writing',
      preference_key: preferenceKey.value.trim(),
      preference_value: preferenceValue.value.trim(),
    })
    preferenceKey.value = ''
    preferenceValue.value = ''
    await loadPreferences()
    ElMessage.success('偏好已保存')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存偏好失败')
  } finally {
    preferenceSaving.value = false
  }
}

const removePreference = async (id) => {
  try {
    await memory.deleteMemoryPreference(id)
    preferences.value = preferences.value.filter((item) => item.id !== id)
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '删除偏好失败')
  }
}

const loadCaseContext = async () => {
  if (!caseId.value) return
  try {
    const { data } = await legalWorkspace.getMatterSummary(caseId.value)
    caseSummary.value = data
    caseTitle.value = data?.matter?.title || data?.case?.title || data?.title || ''
  } catch {
    caseTitle.value = ''
    caseSummary.value = null
  }
}

onMounted(() => {
  connectWS()
  loadPreferences()
  loadCaseContext()
})
onUnmounted(() => {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (ws) ws.close(1000)
})
</script>

<style scoped>
.chat-page {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--space-5);
  height: calc(100vh - 220px);
  min-height: 620px;
}

.chat-sidebar,
.chat-main {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}

.chat-sidebar-head,
.chat-main-head,
.chat-composer {
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border-light);
}

.chat-main-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.chat-sidebar-title,
.chat-main-title {
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 600;
}

.chat-main-title { font-size: var(--text-base); }

.chat-sidebar-sub,
.chat-main-sub {
  margin-top: var(--space-1);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.6;
}

.chat-case {
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
}

.chat-meta-label {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 500;
}

.chat-case-title {
  margin-top: var(--space-1);
  overflow: hidden;
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-case-counts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
  margin-top: var(--space-3);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

.chat-case-counts strong {
  display: block;
  margin-top: 3px;
  color: var(--color-text);
  font-size: var(--text-sm);
}

.chat-link-button {
  margin-top: var(--space-2);
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-weight: 500;
  cursor: pointer;
}

.chat-link-button:hover { text-decoration: underline; }

.chat-sidebar-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: var(--space-3);
}

.chat-session {
  display: block;
  width: 100%;
  margin-bottom: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-primary-light);
  border-radius: var(--radius-lg);
  background: var(--color-primary-light);
  color: var(--color-primary);
  text-align: left;
  cursor: default;
}

.chat-session-title {
  overflow: hidden;
  font-size: var(--text-sm);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-session-id {
  margin-top: var(--space-1);
  color: var(--color-primary);
  font-size: var(--text-xs);
  opacity: 0.75;
}

.chat-message-stats {
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-surface-subtle);
}

.chat-stat-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-top: var(--space-2);
}

.chat-stat-number {
  color: var(--color-text);
  font-size: 24px;
  font-weight: 600;
  line-height: 1;
}

.chat-meta {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.chat-prefs {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}

.chat-prefs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.chat-prefs-list {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.chat-pref-item {
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border-light);
  font-size: var(--text-xs);
}

.chat-pref-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-2);
}

.chat-pref-text { min-width: 0; }

.chat-pref-key {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-pref-value {
  margin-top: 2px;
  color: var(--color-text-muted);
  overflow-wrap: anywhere;
}

.chat-pref-delete {
  flex-shrink: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
}

.chat-pref-delete:hover { color: var(--color-danger); }

.chat-status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-light);
}

.chat-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-text-muted);
}

.chat-dot.is-ready { background: var(--color-success); }
.chat-dot.is-busy { background: var(--color-warning); }
.chat-dot.is-idle { background: var(--color-border); }

.chat-main-actions {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

.chat-connection {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.chat-messages {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: var(--space-5) var(--space-6);
  background: var(--color-surface-subtle);
}

.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  max-width: 560px;
  margin: 0 auto;
  text-align: center;
}

.chat-empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-subtle);
  color: var(--color-text-muted);
}

.chat-empty-title {
  color: var(--color-text);
  font-size: var(--text-lg);
  font-weight: 600;
}

.chat-empty-text {
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}

.chat-message-row {
  display: flex;
  justify-content: flex-start;
  margin-bottom: var(--space-5);
}

.chat-message-row.is-user { justify-content: flex-end; }

.chat-message-stack {
  max-width: 74%;
  min-width: 0;
}

.chat-message-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.chat-message-meta.is-user { justify-content: flex-end; }

.chat-bubble {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
  font-size: var(--text-sm);
  line-height: 1.6;
}

.chat-bubble.is-user {
  border-color: transparent;
  background: var(--color-primary);
  color: #fff;
}

.chat-bubble.is-assistant {
  background: var(--color-surface);
  color: var(--color-text);
}

.chat-bubble-text {
  margin: 0;
  white-space: pre-wrap;
}

.chat-citations {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.chat-citation {
  display: block;
  width: 100%;
  padding: var(--space-3);
  border: 1px solid var(--color-primary-light);
  border-radius: var(--radius-lg);
  background: var(--color-primary-light);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.chat-citation:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
}

.chat-citation-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-1);
}

.chat-citation-title {
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-weight: 600;
}

.chat-citation-page {
  color: var(--color-primary);
  font-size: var(--text-xs);
  opacity: 0.75;
}

.chat-citation-text {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.chat-quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.chat-quick-actions button {
  padding: 5px 8px;
  border: 1px solid var(--color-border);
  border-radius: 3px;
  background: var(--color-surface);
  color: var(--color-primary);
  font-size: 11px;
  cursor: pointer;
}

.chat-quick-actions button:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
}

.chat-composer {
  border-top: 1px solid var(--color-border-light);
  border-bottom: 0;
  background: var(--color-surface);
}

.chat-composer-row {
  display: flex;
  gap: var(--space-3);
}

.send-button {
  min-width: 92px;
  height: 56px;
  border-radius: var(--radius-lg);
}

.cursor-blink { animation: blink 1s step-end infinite; }

.typing-dots {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  margin-top: var(--space-1);
}

.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--color-text-muted);
  animation: typing 1s infinite ease-in-out;
}

.typing-dots span:nth-child(2) { animation-delay: 0.15s; }
.typing-dots span:nth-child(3) { animation-delay: 0.3s; }

:deep(.chat-input .el-textarea__inner) {
  min-height: 56px !important;
  border-radius: var(--radius-lg);
  box-shadow: 0 0 0 1px var(--color-border-light) inset !important;
  background: var(--color-surface);
}

@keyframes blink {
  50% { opacity: 0; }
}

@keyframes typing {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.45; }
  40% { transform: translateY(-3px); opacity: 1; }
}
</style>
