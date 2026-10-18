<template>
  <div class="legal-deadlines">
    <!-- 与后端 _require_case_manager 同判据：创建/操作/列表均限管理员或审核律师，
         不可用操作不展示（ux-audit P1-5 同模式，D12 回归发现） -->
    <section v-if="!canManageDeadlines" class="app-card">
      <div class="app-card-body deadline-permission">
        <el-empty description="关键日期由组织管理员或审核律师管理。" :image-size="80" />
        <p class="app-empty-note deadline-permission-hint">如需创建或调整期限，请联系你的组织管理员或审核律师。</p>
      </div>
    </section>
    <template v-else>
    <p v-if="noCase" class="no-case-hint">当前未选择案件。「创建期限」「期限列表」需在顶部案件选择器中选择案件后使用。</p>

    <section class="app-card">
      <div class="app-card-head">
        <h2>创建期限</h2>
        <span class="app-card-head-meta">类型 / 截止日期 / 负责人 / 提醒</span>
      </div>
      <div class="app-card-body">
        <el-form :model="deadlineForm" label-width="100px" size="small" class="deadline-form" @submit.prevent="submitDeadline">
          <div class="deadline-form-grid">
            <el-form-item label="类型" required>
              <el-select v-model="deadlineForm.deadline_type" class="deadline-field">
                <el-option v-for="t in deadlineTypes" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="截止日期" required>
              <el-input v-model="deadlineForm.deadline_at" class="deadline-field" placeholder="YYYY-MM-DD" />
            </el-form-item>
            <el-form-item label="负责人">
              <el-select v-model="deadlineForm.owner_id" class="deadline-field" placeholder="选择负责人">
                <el-option v-for="m in orgMembers" :key="m.user_id" :label="m.username || String(m.user_id)" :value="m.user_id" />
              </el-select>
            </el-form-item>
            <el-form-item label="提醒">
              <el-select v-model="deadlineForm.reminder_offsets" class="deadline-field" multiple collapse-tags placeholder="选择提醒时间">
                <el-option label="提前1天" :value="1" />
                <el-option label="提前3天" :value="3" />
                <el-option label="提前7天" :value="7" />
                <el-option label="提前14天" :value="14" />
                <el-option label="提前30天" :value="30" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="描述">
            <el-input v-model="deadlineForm.description" type="textarea" :rows="2" placeholder="期限相关说明..." />
          </el-form-item>
          <div class="deadline-form-actions">
            <el-button type="primary" :loading="deadlineLoading" @click="submitDeadline">创建期限</el-button>
          </div>
        </el-form>
      </div>
    </section>

    <section class="app-card">
      <div class="app-card-head">
        <h2>期限列表</h2>
        <div class="deadline-head-tools">
          <span class="app-card-head-meta">{{ deadlineTotal }} 条</span>
          <el-select v-model="deadlineFilter" class="deadline-filter" placeholder="按状态筛选" clearable size="small" @change="loadDeadlines">
            <el-option label="进行中" value="active" />
            <el-option label="已完成" value="completed" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
        </div>
      </div>
      <div class="app-card-body">
        <QueryStateView :status="deadlinesStatus" :error="deadlinesError" empty-text="暂无关键日期" min-height="120px" @retry="loadDeadlines">
        <el-table :data="deadlines" stripe size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="deadline_type" label="类型" width="120">
            <template #default="{ row }">
              <span class="app-tag"><i class="deadline-type-dot" :style="{ background: typeColor(row.deadline_type) }"></i>{{ typeLabel(row.deadline_type) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="截止日期" width="120">
            <template #default="{ row }">{{ row.deadline_at?.slice(0, 10) }}</template>
          </el-table-column>
          <el-table-column label="负责人" width="100">
            <template #default="{ row }">{{ orgMembers.find(m => m.user_id === row.owner_id)?.username || row.owner_id }}</template>
          </el-table-column>
          <el-table-column prop="description" label="描述" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <span class="app-tag" :class="deadlineStatusTagClass(row.status)">{{ deadlineStatusLabel(row.status) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button v-if="row.status === 'active'" size="small" text type="success" @click="completeDeadline(row.id)">完成</el-button>
              <el-button v-if="row.status === 'active'" size="small" text type="warning" @click="cancelDeadline(row.id)">取消</el-button>
              <el-button v-if="row.status === 'active'" size="small" text type="primary" @click="addToCalendar(row.id)">加入日历</el-button>
            </template>
          </el-table-column>
        </el-table>
        </QueryStateView>
        <div class="app-pagination-end">
          <el-pagination
            v-model:current-page="deadlinePage"
            :page-size="deadlinePageSize"
            :total="deadlineTotal"
            layout="total, prev, pager, next"
            small
            @current-change="loadDeadlines"
          />
        </div>
      </div>
    </section>

    <section class="app-card">
      <div class="app-card-head">
        <h2>日历视图</h2>
        <div class="deadline-head-tools">
          <el-button size="small" @click="prevMonth">&lt;</el-button>
          <span class="calendar-month">{{ calendarYear }}年{{ calendarMonth }}月</span>
          <el-button size="small" @click="nextMonth">&gt;</el-button>
        </div>
      </div>
      <div class="app-card-body">
        <div class="calendar-grid">
          <div v-for="d in weekDays" :key="d" class="calendar-header">{{ d }}</div>
          <div
            v-for="(cell, idx) in calendarCells"
            :key="idx"
            class="calendar-cell"
            :class="{ 'other-month': !cell.currentMonth, 'today': cell.isToday, 'has-deadline': !!cell.deadline }"
            @click="cell.deadline && selectDeadline(cell.deadline)"
          >
            <span class="cell-day">{{ cell.day }}</span>
            <div v-if="cell.deadline" class="cell-dot" :style="{ background: typeColor(cell.deadline.deadline_type) }" :title="typeLabel(cell.deadline.deadline_type)"></div>
          </div>
        </div>
        <div class="calendar-legend">
          <span v-for="t in deadlineTypes" :key="t.value" class="legend-item">
            <span class="legend-dot" :style="{ background: typeColor(t.value) }"></span>
            {{ t.label }}
          </span>
        </div>
      </div>
    </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/empty/style/css'
import api from '../api'
import { useAuthStore } from '../stores/auth'
import QueryStateView from '../components/QueryStateView.vue'
import { normalizeError } from '../api/errors.js'

const auth = useAuthStore()

const props = defineProps({
  orgId: { type: Number, required: true },
  caseId: { type: Number, required: false, default: null },
})

const noCase = computed(() => !props.caseId)

// 与后端 _require_case_manager 同判据：仅看 legal_role，不看系统角色（ux-audit P1-5）
const canManageDeadlines = computed(() => ['admin', 'reviewer'].includes(auth.currentUser?.legal_role))

const orgMembers = ref([])
const loadOrgMembers = async () => {
  try {
    const { data } = await api.listOrgMembers(props.orgId)
    orgMembers.value = Array.isArray(data) ? data : (data.members || data.items || [])
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '组织成员加载失败')
  }
}

// --- Deadline form ---
const deadlineTypes = [
  { value: 'hearing', label: '开庭' },
  { value: 'defense', label: '答辩' },
  { value: 'appeal', label: '上诉' },
  { value: 'performance', label: '履行' },
  { value: 'payment', label: '付款' },
  { value: 'expiry', label: '届满' },
  { value: 'custom', label: '自定义' },
]

const typeColor = (t) => ({
  hearing: 'var(--color-cat-hearing)', defense: 'var(--color-cat-defense)', appeal: 'var(--color-cat-appeal)',
  performance: 'var(--color-cat-performance)', payment: 'var(--color-cat-payment)',
  expiry: 'var(--color-cat-expiry)', custom: 'var(--color-cat-custom)',
}[t] || 'var(--color-cat-payment)')

const typeLabel = (t) => deadlineTypes.find(d => d.value === t)?.label || t

const deadlineForm = ref({ deadline_type: 'hearing', deadline_at: '', owner_id: null, description: '', reminder_offsets: [] })
const deadlineLoading = ref(false)

const submitDeadline = async () => {
  if (noCase.value) return ElMessage.warning('请先在顶部选择案件')
  if (!deadlineForm.value.deadline_at) return ElMessage.warning('请选择截止日期')
  if (!deadlineForm.value.owner_id) return ElMessage.warning('请选择负责人')
  deadlineLoading.value = true
  try {
    const payload = {
      deadline_type: deadlineForm.value.deadline_type,
      deadline_at: deadlineForm.value.deadline_at + 'T00:00:00',
      owner_id: deadlineForm.value.owner_id,
      description: deadlineForm.value.description,
      reminder_offsets_json: deadlineForm.value.reminder_offsets.length
        ? JSON.stringify(deadlineForm.value.reminder_offsets)
        : undefined,
    }
    await api.createDeadline(props.orgId, props.caseId, payload)
    ElMessage.success('期限已创建')
    deadlineForm.value = { deadline_type: 'hearing', deadline_at: '', owner_id: null, description: '', reminder_offsets: [] }
    loadDeadlines()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  }
  deadlineLoading.value = false
}

// --- Deadline list ---
const deadlines = ref([])
const deadlinePage = ref(1)
const deadlinePageSize = ref(20)
const deadlineTotal = ref(0)
const deadlineFilter = ref('')
// 三态（ux-audit M-13）：加载失败与"暂无期限"分离，失败可重试；刷新时保留已有列表不闪骨架
const deadlinesLoading = ref(false)
const deadlinesError = ref(null)
const deadlinesStatus = computed(() => {
  if (deadlinesLoading.value && !deadlines.value.length) return 'loading'
  if (deadlinesError.value) return 'error'
  if (!deadlines.value.length) return 'empty'
  return 'success'
})

const loadDeadlines = async () => {
  if (noCase.value) { deadlines.value = []; deadlineTotal.value = 0; deadlinesError.value = null; return }
  deadlinesLoading.value = true
  deadlinesError.value = null
  try {
    const { data } = await api.listDeadlines(props.orgId, props.caseId, deadlineFilter.value, deadlinePage.value, deadlinePageSize.value)
    deadlines.value = data.items || data
    deadlineTotal.value = data.total || (Array.isArray(data) ? data.length : 0)
  } catch (e) {
    deadlinesError.value = normalizeError(e)
  } finally {
    deadlinesLoading.value = false
  }
}

const completeDeadline = async (id) => {
  try {
    await api.patchDeadline(id, { action: 'complete' })
    ElMessage.success('已标记完成')
    loadDeadlines()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

const cancelDeadline = async (id) => {
  try {
    await api.patchDeadline(id, { action: 'cancel' })
    ElMessage.success('已取消')
    loadDeadlines()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

const addToCalendar = async (id) => {
  try {
    await api.deadlineToCalendar(id)
    ElMessage.success('已添加到日历')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '添加日历失败')
  }
}

const selectDeadline = (d) => {
  ElMessage.info(`${typeLabel(d.deadline_type)}：${d.deadline_at?.slice(0, 10)}${d.description ? ' - ' + d.description : ''}`)
}

// --- Calendar ---
const calendarYear = ref(new Date().getFullYear())
const calendarMonth = ref(new Date().getMonth() + 1)
const weekDays = ['一', '二', '三', '四', '五', '六', '日']

const prevMonth = () => {
  if (calendarMonth.value === 1) { calendarMonth.value = 12; calendarYear.value-- }
  else calendarMonth.value--
}

const nextMonth = () => {
  if (calendarMonth.value === 12) { calendarMonth.value = 1; calendarYear.value++ }
  else calendarMonth.value++
}

const calendarCells = computed(() => {
  const y = calendarYear.value
  const m = calendarMonth.value
  const firstDay = new Date(y, m - 1, 1)
  let startWeekday = firstDay.getDay() - 1
  if (startWeekday < 0) startWeekday = 6
  const daysInMonth = new Date(y, m, 0).getDate()
  const prevMonthDays = new Date(y, m - 1, 0).getDate()

  const deadlineMap = {}
  deadlines.value.forEach(d => {
    if (d.deadline_at) {
      const key = d.deadline_at.slice(0, 10)
      if (!deadlineMap[key]) deadlineMap[key] = d
    }
  })

  const today = new Date()
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`

  const cells = []
  for (let i = 0; i < startWeekday; i++) {
    cells.push({ day: prevMonthDays - startWeekday + 1 + i, currentMonth: false, deadline: null, isToday: false })
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    cells.push({ day: d, currentMonth: true, deadline: deadlineMap[dateStr] || null, isToday: dateStr === todayStr })
  }
  const remaining = 42 - cells.length
  for (let i = 1; i <= remaining; i++) {
    cells.push({ day: i, currentMonth: false, deadline: null, isToday: false })
  }
  return cells
})

// 方向 B：状态并入共享 .app-tag 变体（进行中/已完成/中性），不再用 el-tag 的 type 色板
const deadlineStatusTagClass = (s) => ({ active: 'is-pending', completed: 'is-ok', cancelled: '' }[s] || '')
const deadlineStatusLabel = (s) => ({ active: '进行中', completed: '已完成', cancelled: '已取消' }[s] || s)

onMounted(() => {
  loadDeadlines()
  loadOrgMembers()
})
</script>

<style scoped>
.legal-deadlines {
  display: grid;
  gap: var(--space-4);
}

.no-case-hint {
  padding: 10px 14px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  background: var(--color-warning-light);
  color: var(--color-warning);
  font-size: var(--text-sm);
}

.deadline-permission {
  display: grid;
  gap: var(--space-2);
  justify-items: center;
}

.deadline-permission-hint {
  margin: 0;
  text-align: center;
}

.deadline-field {
  width: 100%;
}

.deadline-form :deep(.el-form-item) {
  margin-bottom: var(--space-3);
}

.deadline-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: var(--space-4);
}

.deadline-form-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.deadline-head-tools {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.deadline-head-tools .app-card-head-meta {
  margin-left: 0;
}

.deadline-filter {
  width: 140px;
}

.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px;
}

.calendar-header {
  padding: var(--space-2) 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-weight: 600;
  text-align: center;
}

.calendar-cell {
  position: relative;
  min-height: 62px;
  padding: 6px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  cursor: default;
}

.calendar-cell.has-deadline {
  cursor: pointer;
}

.calendar-cell.other-month {
  background: var(--color-surface-subtle);
  opacity: 0.55;
}

.calendar-cell.today {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
}

.cell-day {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
}

.cell-dot {
  position: absolute;
  right: 6px;
  bottom: 6px;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
}

.calendar-month {
  min-width: 90px;
  color: var(--color-text);
  font-size: var(--text-base);
  font-weight: 600;
  text-align: center;
}

.calendar-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  margin-top: var(--space-3);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

.legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: var(--radius-full);
}

.deadline-type-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: var(--radius-full);
}

@media (max-width: 720px) {
  .deadline-form-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
