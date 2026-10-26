<template>
  <div class="app-section-intro tab-intro">
    <strong>通知模板</strong>
    <span>改审核/咨询邮件文案，不改版式：layout 模式只覆写导语、按钮、脚注与主题；也可整段自定义 HTML 或退回纯文本。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <div class="app-empty-note">
          内置 7 个版式（代码里）。这里保存的是数据库覆写版本：新建为草稿，启用后才生效；同模板键只保留一个生效版本。
        </div>
        <div class="app-filter-bar-actions">
          <el-button :loading="loading" @click="fetchTemplates">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>邮件模板</h2>
      <span class="app-card-head-meta">
        深链按钮：{{ actionUrlConfigured ? '已配置' : '未配置（预览/发送都不渲染按钮）' }}
      </span>
    </div>
    <div class="app-card-body">
      <div class="app-table-wrap">
        <QueryStateView :status="tableStatus" :error="error" empty-text="暂无模板" min-height="160px" @retry="fetchTemplates">
          <el-table :data="rows" border size="small" max-height="420">
            <el-table-column prop="template_key" label="模板键" width="190" />
            <el-table-column prop="lead" label="当前导语" min-width="220" show-overflow-tooltip />
            <el-table-column label="生效版本" width="150">
              <template #default="{ row }">
                <span class="app-tag" :class="row.active ? 'is-info' : ''">
                  {{ row.active ? `覆写 v${row.active.version} · ${bodyFormatLabel(row.active.body_format)}` : '内置版式' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="更新" width="120">
              <template #default="{ row }"><span class="app-num">{{ formatTime(row.active?.updated_at) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button link type="primary" @click="openEditor(row)">编辑文案</el-button>
              </template>
            </el-table-column>
          </el-table>
        </QueryStateView>
      </div>
    </div>
  </section>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>版本历史</h2>
      <span class="app-card-head-meta">草稿可启用；启用后旧版本自动转 retired</span>
    </div>
    <div class="app-card-body">
      <div class="app-table-wrap">
        <QueryStateView :status="historyStatus" :error="error" empty-text="还没有数据库覆写版本" min-height="140px" @retry="fetchTemplates">
          <el-table :data="templates" border size="small" max-height="320">
            <el-table-column prop="template_key" label="模板键" width="190" />
            <el-table-column prop="locale" label="语言" width="80" />
            <el-table-column prop="version" label="版本" width="70">
              <template #default="{ row }"><span class="app-num">v{{ row.version }}</span></template>
            </el-table-column>
            <el-table-column label="格式" width="90">
              <template #default="{ row }">{{ bodyFormatLabel(row.body_format) }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="90" />
            <el-table-column label="更新时间" width="150">
              <template #default="{ row }"><span class="app-num">{{ formatTime(row.updated_at) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button
link type="primary" :disabled="row.status === 'active'"
                           :loading="activating === row.id" @click="activate(row)">
                  {{ row.status === 'active' ? '生效中' : '启用' }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </QueryStateView>
      </div>
    </div>
  </section>

  <el-dialog v-model="editorVisible" :title="`编辑文案 · ${editor.template_key}`" width="920px" destroy-on-close>
    <div class="tpl-editor">
      <div class="tpl-editor-form">
        <div class="tpl-field">
          <span class="tpl-label">格式</span>
          <el-radio-group v-model="editor.body_format">
            <el-radio-button value="layout">保留版式</el-radio-button>
            <el-radio-button value="html">自定义 HTML</el-radio-button>
            <el-radio-button value="text">纯文本</el-radio-button>
          </el-radio-group>
        </div>

        <template v-if="editor.body_format === 'layout'">
          <div class="tpl-field">
            <span v-pre class="tpl-label">邮件主题（支持 {{ title }} 占位）</span>
            <el-input v-model="editor.subject" :placeholder="'【律智检】{{ title }}'" />
          </div>
          <div class="tpl-field">
            <span class="tpl-label">导语</span>
            <el-input v-model="editor.lead" type="textarea" :rows="2" />
          </div>
          <div class="tpl-field">
            <span class="tpl-label">按钮文案</span>
            <el-input v-model="editor.action_label" placeholder="去审核" />
          </div>
          <div class="tpl-field">
            <span class="tpl-label">脚注</span>
            <el-input v-model="editor.footnote" type="textarea" :rows="2" />
          </div>
        </template>

        <template v-else>
          <div class="tpl-field">
            <span v-pre class="tpl-label">邮件主题（支持 {{ title }} 占位）</span>
            <el-input v-model="editor.subject_template" :placeholder="'【律智检】{{ title }}'" />
          </div>
          <div class="tpl-field">
            <span class="tpl-label">{{ editor.body_format === 'html' ? 'HTML 正文' : '纯文本正文' }}</span>
            <el-input
              v-model="editor.body_template"
              type="textarea"
              :rows="8"
              :placeholder="editor.body_format === 'html' ? '<h1>{{ title }}</h1><p>{{ body }}</p>' : '{{ title }}\n{{ body }}'"
            />
          </div>
        </template>

        <div class="tpl-actions">
          <el-button :loading="previewing" @click="runPreview">预览</el-button>
          <el-button type="primary" :loading="saving" @click="saveDraft">保存草稿</el-button>
          <el-button v-if="draftId" type="success" :loading="activating === draftId" @click="activateDraft">启用该版本</el-button>
        </div>
      </div>

      <div class="tpl-editor-preview">
        <div class="tpl-preview-head">
          <strong>预览</strong>
          <span class="app-num">{{ preview.subject || '（点「预览」渲染）' }}</span>
        </div>
        <iframe v-if="preview.html" class="tpl-preview-frame" sandbox="" :srcdoc="preview.html"></iframe>
        <pre v-else class="tpl-preview-text">{{ preview.text || '纯文本模式没有 HTML 版式，预览显示纯文本。' }}</pre>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import QueryStateView from '../QueryStateView.vue'

const loading = ref(false)
const error = ref(null)
const builtin = ref([])
const templates = ref([])
const activeOverrides = ref([])
const actionUrlConfigured = ref(false)
const activating = ref(0)

const editorVisible = ref(false)
const previewing = ref(false)
const saving = ref(false)
const draftId = ref(0)
const preview = ref({ subject: '', text: '', html: '' })
const editor = ref({
  template_key: '',
  body_format: 'layout',
  subject: '',
  lead: '',
  action_label: '',
  footnote: '',
  subject_template: '',
  body_template: '',
})

const activeByKey = computed(() => {
  const map = {}
  for (const item of activeOverrides.value) map[item.template_key] = item
  return map
})

const rows = computed(() => builtin.value.map((item) => {
  const active = activeByKey.value[item.template_key] || null
  const customBody = Boolean(active) && active.body_format !== 'layout'
  return {
    ...item,
    active,
    // 只有 layout 覆写还能展示「导语」；html/text 整段替换正文，展示内置导语会误导
    lead: customBody ? '（自定义正文，版式由模板决定）' : (active ? activeLead(active) || item.lead : item.lead),
  }
}))

const tableStatus = computed(() => {
  if (loading.value && !builtin.value.length) return 'loading'
  if (error.value) return 'error'
  if (!builtin.value.length) return 'empty'
  return 'success'
})
const historyStatus = computed(() => {
  if (loading.value && !templates.value.length) return 'loading'
  if (error.value) return 'error'
  if (!templates.value.length) return 'empty'
  return 'success'
})

const bodyFormatLabel = (value) => ({ layout: '保留版式', html: 'HTML', text: '纯文本' }[value] || value || '纯文本')

const parseLayout = (raw) => {
  try {
    const parsed = JSON.parse(raw || '{}')
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}
const activeLead = (row) => (row.body_format === 'layout' ? parseLayout(row.body_template).lead : '')

const formatTime = (value) => {
  if (!value) return '-'
  const text = String(value).replace('T', ' ')
  return /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(text) ? text.slice(5, 16) : text
}

const fetchTemplates = async () => {
  loading.value = true
  error.value = null
  try {
    const { data } = await api.notificationTemplates()
    builtin.value = data?.builtin || []
    templates.value = data?.templates || []
    activeOverrides.value = data?.active_overrides || []
    actionUrlConfigured.value = Boolean(data?.action_url_configured)
  } catch (err) {
    error.value = err
    builtin.value = []
    templates.value = []
    activeOverrides.value = []
    ElMessage.error(err.response?.data?.message || '获取通知模板失败')
  } finally {
    loading.value = false
  }
}

const openEditor = (row) => {
  const active = row.active
  const layout = active && active.body_format === 'layout' ? parseLayout(active.body_template) : {}
  editor.value = {
    template_key: row.template_key,
    body_format: active?.body_format || 'layout',
    subject: layout.subject || row.subject || '',
    lead: layout.lead || row.lead || '',
    action_label: layout.action_label || row.action_label || '',
    footnote: layout.footnote || row.footnote || '',
    subject_template: active?.subject_template || row.subject || '',
    body_template: active && active.body_format !== 'layout' ? active.body_template : '',
  }
  draftId.value = 0
  preview.value = { subject: '', text: '', html: '' }
  editorVisible.value = true
}

const buildPayload = () => {
  const state = editor.value
  if (state.body_format === 'layout') {
    return {
      template_key: state.template_key,
      body_format: 'layout',
      layout: {
        subject: state.subject,
        lead: state.lead,
        action_label: state.action_label,
        footnote: state.footnote,
      },
    }
  }
  return {
    template_key: state.template_key,
    body_format: state.body_format,
    subject_template: state.subject_template,
    body_template: state.body_template,
  }
}

const runPreview = async () => {
  previewing.value = true
  try {
    const { data } = await api.previewNotificationTemplate(buildPayload())
    preview.value = {
      subject: data?.subject || '',
      text: data?.text || '',
      html: data?.html || '',
    }
  } catch (err) {
    ElMessage.error(err.response?.data?.message || err.response?.data?.error?.detail || '预览失败')
  } finally {
    previewing.value = false
  }
}

const saveDraft = async () => {
  saving.value = true
  try {
    const { data } = await api.createNotificationTemplate({ ...buildPayload(), locale: 'zh-CN' })
    draftId.value = data?.id || 0
    ElMessage.success(`草稿已保存（v${data?.version}），确认后点「启用该版本」`)
    await fetchTemplates()
  } catch (err) {
    ElMessage.error(err.response?.data?.message || '保存草稿失败')
  } finally {
    saving.value = false
  }
}

const activateDraft = async () => {
  if (!draftId.value) return
  activating.value = draftId.value
  try {
    await api.activateNotificationTemplate(draftId.value)
    ElMessage.success('已启用，新的通知将使用该文案')
    draftId.value = 0
    await fetchTemplates()
  } catch (err) {
    ElMessage.error(err.response?.data?.message || '启用失败')
  } finally {
    activating.value = 0
  }
}

const activate = async (row) => {
  activating.value = row.id
  try {
    await api.activateNotificationTemplate(row.id)
    ElMessage.success(`已启用 v${row.version}`)
    await fetchTemplates()
  } catch (err) {
    ElMessage.error(err.response?.data?.message || '启用失败')
  } finally {
    activating.value = 0
  }
}

// 切格式后旧预览/旧草稿都不再对应同一份内容：清掉，避免「预览 layout、保存 html」
watch(() => editor.value.body_format, () => {
  preview.value = { subject: '', text: '', html: '' }
  draftId.value = 0
})

onMounted(fetchTemplates)
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.tpl-editor {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
}
.tpl-editor-form {
  display: grid;
  gap: var(--space-3);
  align-content: start;
}
.tpl-field {
  display: grid;
  gap: var(--space-1);
}
.tpl-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.tpl-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.tpl-editor-preview {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: var(--space-2);
  min-width: 0;
}
.tpl-preview-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: 13px;
}
.tpl-preview-frame {
  width: 100%;
  height: 460px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: #fff;
}
.tpl-preview-text {
  margin: 0;
  height: 460px;
  overflow: auto;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  font-size: 12px;
  line-height: 20px;
  white-space: pre-wrap;
}
@media (max-width: 900px) {
  .tpl-editor {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
