<template>
  <div class="ai-feedback">
    <div v-if="feedbackValue !== null && feedbackValue !== undefined" class="feedback-done">
      <el-tag size="small" :type="feedbackValue === 1 ? 'success' : 'info'" effect="plain">
        {{ feedbackValue === 1 ? '已收到反馈：有帮助' : '已收到反馈：待改进' }}
      </el-tag>
      <span class="feedback-thanks">感谢您的评价，反馈将用于改进 AI 质量。</span>
    </div>
    <div v-else class="feedback-row">
      <span class="feedback-label">这个结果对您有帮助吗？</span>
      <el-button size="small" :loading="submitting === 'positive'" @click="submitPositive">
        有帮助
      </el-button>
      <el-button size="small" :loading="submitting === 'negative'" @click="openNegative">
        待改进
      </el-button>
      <div v-if="showNegative" class="feedback-panel">
        <el-select v-model="reason" placeholder="选择原因" size="small" clearable>
          <el-option v-for="r in reasons" :key="r" :label="r" :value="r" />
        </el-select>
        <el-input
          v-model="note"
          type="textarea"
          :rows="2"
          placeholder="补充说明（可选）"
          maxlength="500"
          show-word-limit
        />
        <div class="feedback-actions">
          <el-button size="small" type="primary" :loading="submitting === 'negative'" @click="submitNegative">
            提交反馈
          </el-button>
          <el-button size="small" @click="cancelNegative">取消</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElInput } from 'element-plus/es/components/input/index'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/input/style/css'

const props = defineProps({
  targetType: { type: String, default: 'consultation' },
  targetId: { type: Number, required: true },
  value: { type: Number, default: null }, // 1 = 有帮助, -1 = 待改进, null = 未反馈
})
const emit = defineEmits(['submit'])

const reasons = ['结论不准确', '引用依据错误或失效', '信息不够充分', '风险等级判断不合理', '其他']
const showNegative = ref(false)
const reason = ref('')
const note = ref('')
const submitting = ref('')
const feedbackValue = ref(props.value)

watch(() => props.value, (v) => { feedbackValue.value = v })

const submitPositive = () => {
  submitting.value = 'positive'
  emit('submit', 1, null)
}

const openNegative = () => {
  showNegative.value = true
}

const cancelNegative = () => {
  showNegative.value = false
  reason.value = ''
  note.value = ''
}

const submitNegative = () => {
  const noteText = [reason.value ? `[${reason.value}]` : '', note.value.trim()].filter(Boolean).join(' ')
  submitting.value = 'negative'
  emit('submit', -1, noteText || null)
}
</script>

<style scoped>
.ai-feedback {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.feedback-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.feedback-label {
  font-size: 13px;
  color: var(--color-text-muted);
}

.feedback-panel {
  display: grid;
  gap: 8px;
  width: 100%;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  margin-top: 4px;
}

.feedback-actions {
  display: flex;
  gap: 8px;
}

.feedback-done {
  display: flex;
  align-items: center;
  gap: 10px;
}

.feedback-thanks {
  font-size: 12px;
  color: var(--color-text-muted);
}
</style>
