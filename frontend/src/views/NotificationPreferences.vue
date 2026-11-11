<template>
  <section class="legal-page pref-page">
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">通知偏好</h1>
        <p class="app-page-sub">按事件类型控制渠道与静默，或用请假代理一次覆盖全部事件；站内通知始终开启。</p>
      </div>
      <div class="app-page-head-actions">
        <button class="pref-link" type="button" @click="router.push('/notifications')">返回通知中心</button>
      </div>
    </header>

    <section class="app-card">
      <div class="app-card-head">
        <h2>请假代理（全部事件）</h2>
        <span class="app-card-head-meta">请假期间所有事件都转给代理，优先于下面按事件类型的设置</span>
      </div>
      <div class="app-card-body">
        <p v-if="leaveLoading" class="pref-state" role="status">正在加载请假设置…</p>
        <div v-else-if="leaveError" class="pref-state pref-state-error" role="alert">
          <span>{{ leaveError.message || '请假设置加载失败' }}</span>
          <button class="pref-link" type="button" @click="loadLeave">重试</button>
        </div>
        <div v-else class="leave-body">
          <div class="leave-status" :class="`leave-status-${leaveStatus}`">
            <span class="leave-pill">{{ statusLabel }}</span>
            <span v-if="leaveSummaryText" class="leave-summary">{{ leaveSummaryText }}</span>
            <span v-else class="leave-summary">未设置请假代理，通知按下面的按事件类型设置发送。</span>
          </div>

          <div class="pref-grid">
            <label class="pref-field">
              <span>请假代理</span>
              <select v-model="leaveForm.delegate_user_id">
                <option :value="null">不设置</option>
                <option v-for="member in members" :key="member.user_id" :value="member.user_id">
                  {{ member.full_name || member.username || `用户 ${member.user_id}` }}
                </option>
              </select>
            </label>
            <label class="pref-field">
              <span>备注</span>
              <input
                v-model="leaveForm.note"
                type="text"
                maxlength="200"
                placeholder="如：出差、年假"
              />
            </label>
          </div>

          <div class="pref-grid">
            <label class="pref-field">
              <span>请假开始</span>
              <input v-model="leaveForm.leave_start" type="date" />
            </label>
            <label class="pref-field">
              <span>请假结束</span>
              <input v-model="leaveForm.leave_end" type="date" />
            </label>
          </div>
          <p class="pref-hint">留空表示长期有效；起止日期都含当天，按「默认（全部事件）」偏好的时区计算。到期提醒只发给你本人，不会转给代理。</p>

          <div class="pref-actions">
            <button class="pref-primary" type="button" :disabled="leaveSaving" @click="saveLeave">
              {{ leaveSaving ? '保存中…' : '保存请假' }}
            </button>
            <button
              class="pref-link"
              type="button"
              :disabled="leaveSaving || leaveStatus === 'none'"
              @click="endLeave"
            >
              结束请假
            </button>
            <span v-if="leaveSavedTip" class="leave-saved" role="status">{{ leaveSavedTip }}</span>
          </div>
          <p v-if="leaveStatus === 'expired'" class="pref-hint pref-hint-warn">
            请假已结束，通知已恢复发送给你；到期任务会自动清理这条记录，也可以手动结束请假。
          </p>
        </div>
      </div>
    </section>

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
            <p class="pref-hint">命中静默时站内立即送达，邮件等外部渠道延后到窗口结束发送；逾期升级等强提醒会穿透静默。按下方时区解释，跨夜可填 22:00–07:00。</p>

            <label class="pref-field">
              <span>时区</span>
              <select v-model="form.timezone">
                <option v-for="zone in timezoneOptions" :key="zone" :value="zone">{{ zone }}</option>
              </select>
            </label>
            <p class="pref-hint">时区用于任务到期提醒判断「今天/明天」，以及静默窗口的解释。</p>

            <label class="pref-field">
              <span>代理接收人</span>
              <select v-model="form.delegate_user_id">
                <option :value="null">不设置</option>
                <option v-for="member in members" :key="member.user_id" :value="member.user_id">
                  {{ member.full_name || member.username || `用户 ${member.user_id}` }}
                </option>
              </select>
            </label>
            <p class="pref-hint">设置后该事件的通知改发代理，正文标注「代 X 接收」；代理只跳一层，逾期升级等强提醒仍直达本人。</p>

            <div class="pref-grid">
              <label class="pref-field">
                <span>代理开始</span>
                <input v-model="form.delegate_start" type="date" />
              </label>
              <label class="pref-field">
                <span>代理结束</span>
                <input v-model="form.delegate_end" type="date" />
              </label>
            </div>
            <p class="pref-hint">留空表示长期有效；起止日期都含当天，按上方时区的当地日期计算。</p>

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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import api from '../api'
import { errorMessage, normalizeError } from '../api/errors.js'
import { useAuthStore } from '../stores/auth'
import {
  buildLeavePayload,
  leaveStatusLabel,
  leaveSummary,
  toLeaveForm,
  validateLeaveForm,
} from '../utils/notificationLeave'
import {
  EVENT_TYPES,
  TIMEZONES,
  buildPreferencePayload,
  toPreferenceForm,
  validatePreferenceForm,
} from '../utils/notificationPreferences'

const router = useRouter()
const authStore = useAuthStore()
const eventTypes = EVENT_TYPES

const selected = ref('all')
const prefs = ref({})
const form = reactive(toPreferenceForm(null))
const loading = ref(true)
const saving = ref(false)
const loadError = ref(null)
const savedTip = ref('')
const members = ref([])

// 请假代理：用户级设置，覆盖全部事件类型，优先级高于按事件类型的代理。
const leaveData = ref(null)
const leaveForm = reactive(toLeaveForm(null))
const leaveLoading = ref(true)
const leaveSaving = ref(false)
const leaveError = ref(null)
const leaveSavedTip = ref('')

const orgId = computed(() => authStore.user?.organization_id || null)

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

const leaveStatus = computed(() => leaveData.value?.status || 'none')
const statusLabel = computed(() => leaveStatusLabel(leaveStatus.value))
const leaveSummaryText = computed(() => leaveSummary(leaveData.value))

const applyForm = (pref) => {
  Object.assign(form, toPreferenceForm(pref))
}

const selectType = (value) => {
  selected.value = value
  savedTip.value = ''
  applyForm(prefs.value[value] || null)
}

let membersOrg = null
const loadMembers = async () => {
  const currentOrg = orgId.value
  if (!currentOrg || membersOrg === currentOrg) return
  try {
    const { data } = await api.listOrgMembers(currentOrg)
    members.value = data || []
    membersOrg = currentOrg
  } catch {
    members.value = []
  }
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
    await loadMembers()
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

const loadLeave = async () => {
  leaveLoading.value = true
  leaveError.value = null
  try {
    const { data } = await api.getNotificationLeave()
    leaveData.value = data || null
    Object.assign(leaveForm, toLeaveForm(data))
    await loadMembers()
  } catch (err) {
    leaveError.value = normalizeError(err)
  } finally {
    leaveLoading.value = false
  }
}

const saveLeave = async () => {
  if (!leaveForm.delegate_user_id) {
    // 选「不设置」再保存等价于结束请假，避免用户卡在一条校验里无路可走。
    await endLeave()
    return
  }
  const invalid = validateLeaveForm(leaveForm)
  if (invalid) {
    ElMessage.warning(invalid)
    return
  }
  leaveSaving.value = true
  leaveSavedTip.value = ''
  try {
    const { data } = await api.updateNotificationLeave(buildLeavePayload(leaveForm))
    leaveData.value = data || null
    Object.assign(leaveForm, toLeaveForm(data))
    leaveSavedTip.value = '已保存'
    ElMessage.success('请假设置已保存')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    leaveSaving.value = false
  }
}

const endLeave = async () => {
  leaveSaving.value = true
  leaveSavedTip.value = ''
  try {
    const { data } = await api.clearNotificationLeave()
    leaveData.value = data || null
    Object.assign(leaveForm, toLeaveForm(data))
    leaveSavedTip.value = '已结束请假'
    ElMessage.success('已结束请假')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    leaveSaving.value = false
  }
}

watch(orgId, () => {
  loadMembers()
})

onMounted(() => {
  load()
  loadLeave()
})
</script>

<style scoped>
.pref-page { display: grid; gap: var(--space-5); padding-bottom: var(--space-8); }
.leave-body { display: grid; gap: var(--space-4); max-width: 640px; }
.leave-status { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); }
.leave-pill { display: inline-flex; align-items: center; height: 22px; padding: 0 var(--space-2); border-radius: 999px; background: var(--color-surface-hover); color: var(--color-text-secondary); font-size: var(--text-xs); font-weight: 600; }
.leave-status-active .leave-pill { background: var(--color-primary-subtle); color: var(--color-primary); }
.leave-status-scheduled .leave-pill { background: var(--color-warning-light); color: var(--color-warning); }
.leave-status-expired .leave-pill { background: var(--color-danger-light); color: var(--color-danger); }
.leave-summary { color: var(--color-text-muted); font-size: var(--text-sm); }
.leave-saved { color: var(--color-success); font-size: var(--text-sm); }
.pref-hint-warn { color: var(--color-danger); }
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
