<template>
  <section class="legal-page pref-page">
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">通知偏好</h1>
        <p class="app-page-sub">按事件类型控制邮件渠道、静默时段与时区；站内通知始终开启。</p>
      </div>
      <div class="app-page-head-actions">
        <button class="pref-link" type="button" @click="router.push('/notifications')">返回通知中心</button>
      </div>
    </header>

    <section class="app-card">
      <div class="app-card-head">
        <h2>事件类型</h2>
        <span class="app-card-head-meta">未单独配置的类型沿用「默认（全部事件）」</span>
      </div>
      <div class="app-card-body">
        <p v-if="loading" class="pref-state" role="status">正在加载通知偏好…</p>
        <div v-else-if="loadError" class="pref-state pref-state-error" role="alert">
          <span>{{ loadError.message || '通知偏好加载失败' }}</span>
          <button class="pref-link" type="button" @click="load">重试</button>
        </div>
        <div v-else class="pref-layout">
          <nav class="pref-nav" aria-label="事件类型">
            <button
              v-for="item in eventTypes"
              :key="item.value"
              type="button"
              class="pref-nav-item"
              :class="{ active: selected === item.value }"
              @click="selectType(item.value)"
            >
              <span class="pref-nav-label">{{ item.label }}</span>
              <span class="pref-nav-state">{{ statusOf(item.value) }}</span>
            </button>
          </nav>

          <form class="pref-form" @submit.prevent="save">
            <header class="pref-form-head">
              <h3>{{ currentLabel }}</h3>
              <p>{{ currentHint }}</p>
            </header>

            <fieldset class="pref-fieldset">
              <legend>通知渠道</legend>
              <label class="pref-check">
                <input type="checkbox" checked disabled />
                <span>站内通知</span>
                <em>始终开启</em>
              </label>
              <label class="pref-check">
                <input v-model="form.email" type="checkbox" />
                <span>邮件</span>
                <em>需组织配置外发邮件</em>
              </label>
            </fieldset>

            <div class="pref-grid">
              <label class="pref-field">
                <span>静默开始</span>
                <input v-model="form.mute_start" type="time" step="60" />
              </label>
              <label class="pref-field">
                <span>静默结束</span>
                <input v-model="form.mute_end" type="time" step="60" />
              </label>
            </div>
            <p class="pref-hint">静默时段目前只对系统投递告警生效；任务/审核邮件仍会发送。按下方时区解释，跨夜可填 22:00–07:00。</p>

            <label class="pref-field">
              <span>时区</span>
              <select v-model="form.timezone">
                <option v-for="zone in timezoneOptions" :key="zone" :value="zone">{{ zone }}</option>
              </select>
            </label>
            <p class="pref-hint">时区用于任务到期提醒判断「今天/明天」，以及静默窗口的解释。</p>

            <div class="pref-actions">
              <button class="pref-primary" type="submit" :disabled="saving">
                {{ saving ? '保存中…' : '保存偏好' }}
              </button>
              <button class="pref-link" type="button" :disabled="saving" @click="resetCurrent">重置</button>
              <span v-if="savedTip" class="pref-saved" role="status">{{ savedTip }}</span>
            </div>
            <p class="pref-hint">切换事件类型会丢弃未保存的修改。</p>
          </form>
        </div>
      </div>
    </section>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import api from '../api'
import { errorMessage, normalizeError } from '../api/errors.js'
import {
  EVENT_TYPES,
  TIMEZONES,
  buildPreferencePayload,
  toPreferenceForm,
  validatePreferenceForm,
} from '../utils/notificationPreferences'

const router = useRouter()
const eventTypes = EVENT_TYPES

const selected = ref('all')
const prefs = ref({})
const form = reactive(toPreferenceForm(null))
const loading = ref(true)
const saving = ref(false)
const loadError = ref(null)
const savedTip = ref('')

const currentLabel = computed(
  () => eventTypes.find((item) => item.value === selected.value)?.label || selected.value,
)
const currentHint = computed(
  () => eventTypes.find((item) => item.value === selected.value)?.hint || '',
)
const timezoneOptions = computed(() => {
  const zones = new Set(TIMEZONES)
  if (form.timezone) zones.add(form.timezone)
  return [...zones]
})

const statusOf = (value) => (prefs.value[value] ? '已配置' : value === 'all' ? '默认' : '未配置')

const applyForm = (pref) => {
  Object.assign(form, toPreferenceForm(pref))
}

const selectType = (value) => {
  selected.value = value
  savedTip.value = ''
  applyForm(prefs.value[value] || null)
}

const load = async () => {
  loading.value = true
  loadError.value = null
  try {
    const { data } = await api.getNotificationPreferences()
    const map = {}
    for (const item of data || []) map[item.event_type] = item
    prefs.value = map
    applyForm(map[selected.value] || null)
  } catch (err) {
    loadError.value = normalizeError(err)
  } finally {
    loading.value = false
  }
}

const save = async () => {
  const invalid = validatePreferenceForm(form)
  if (invalid) {
    ElMessage.warning(invalid)
    return
  }
  saving.value = true
  savedTip.value = ''
  try {
    const { data } = await api.updateNotificationPreference(
      selected.value,
      buildPreferencePayload(form),
    )
    prefs.value = { ...prefs.value, [selected.value]: data }
    applyForm(data)
    savedTip.value = '已保存'
    ElMessage.success('通知偏好已保存')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

const resetCurrent = () => {
  applyForm(prefs.value[selected.value] || null)
  savedTip.value = ''
}

onMounted(load)
</script>

<style scoped>
.pref-page { display: grid; gap: var(--space-5); padding-bottom: var(--space-8); }
.pref-layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: var(--space-6); }
.pref-nav { display: grid; align-content: start; gap: 4px; padding-right: var(--space-4); border-right: 1px solid var(--color-border-light); }
.pref-nav-item { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); min-height: 38px; padding: 0 var(--space-3); border: 0; border-radius: var(--radius-md); background: transparent; color: var(--color-text); font: inherit; font-size: var(--text-sm); text-align: left; cursor: pointer; }
.pref-nav-item:hover { background: var(--color-surface-hover); }
.pref-nav-item.active { background: var(--color-primary-subtle); color: var(--color-primary); font-weight: 600; }
.pref-nav-state { flex: 0 0 auto; color: var(--color-text-muted); font-size: var(--text-xs); font-weight: 400; }
.pref-nav-item.active .pref-nav-state { color: var(--color-primary); }
.pref-form { display: grid; align-content: start; gap: var(--space-4); max-width: 640px; }
.pref-form-head h3 { margin: 0; font-size: var(--text-lg); }
.pref-form-head p { margin: 4px 0 0; color: var(--color-text-muted); font-size: var(--text-sm); }
.pref-fieldset { display: grid; gap: var(--space-2); margin: 0; padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); }
.pref-fieldset legend { padding: 0 var(--space-1); color: var(--color-text-muted); font-size: var(--text-xs); }
.pref-check { display: flex; align-items: center; gap: var(--space-2); font-size: var(--text-sm); }
.pref-check em { color: var(--color-text-muted); font-size: var(--text-xs); font-style: normal; }
.pref-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
.pref-field { display: grid; gap: 6px; color: var(--color-text-secondary); font-size: var(--text-xs); }
.pref-field input, .pref-field select { height: 34px; padding: 0 var(--space-2); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; font-size: var(--text-sm); }
.pref-field input:focus, .pref-field select:focus { border-color: var(--color-primary); outline: 2px solid var(--color-primary-subtle); }
.pref-hint { margin: -4px 0 0; color: var(--color-text-muted); font-size: var(--text-xs); line-height: 1.6; }
.pref-actions { display: flex; align-items: center; gap: var(--space-3); padding-top: var(--space-2); }
.pref-primary { height: 34px; padding: 0 var(--space-4); border: 0; border-radius: var(--radius-md); background: var(--color-primary); color: #fff; font: inherit; font-size: var(--text-sm); font-weight: 600; cursor: pointer; }
.pref-primary:hover:not(:disabled) { background: var(--color-primary-hover); }
.pref-primary:disabled { opacity: 0.6; cursor: wait; }
.pref-link { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: var(--text-sm); cursor: pointer; }
.pref-link:hover:not(:disabled) { color: var(--color-primary-hover); text-decoration: underline; }
.pref-link:disabled { color: var(--color-text-muted); cursor: default; }
.pref-saved { color: var(--color-success); font-size: var(--text-sm); }
.pref-state { display: flex; align-items: center; gap: var(--space-3); color: var(--color-text-muted); font-size: var(--text-sm); }
.pref-state-error { color: var(--color-danger); }
@media (max-width: 900px) {
  .pref-layout { grid-template-columns: 1fr; }
  .pref-nav { grid-template-columns: repeat(2, minmax(0, 1fr)); padding-right: 0; padding-bottom: var(--space-3); border-right: 0; border-bottom: 1px solid var(--color-border-light); }
  .pref-grid { grid-template-columns: 1fr; }
}
</style>
