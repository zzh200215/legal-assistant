<template>
  <el-card v-if="records.length" class="panel-card">
    <template #header>
      <div class="card-header-inline">
        <span>问答历史</span>
        <el-button text @click="$emit('refresh')">刷新</el-button>
      </div>
    </template>
    <div v-if="records.length" class="stack-list">
      <div v-for="item in records" :key="`qa-${item.id}`" class="stack-item">
        <div class="stack-top">
          <strong>Q: {{ item.question }}</strong>
          <el-tag size="small">{{ item.source === 'chat' ? '聊天问答' : '文档问答' }}</el-tag>
          <el-tag v-if="item.latency_ms" size="small" type="info">{{ item.latency_ms }}ms</el-tag>
          <el-tag v-if="item.feedback_value" size="small" :type="feedbackTagType(item.feedback_value)">
            {{ feedbackValueText(item.feedback_value) }}
          </el-tag>
          <el-tag v-if="item.feedback_status === 'open'" size="small" type="warning">待处理</el-tag>
          <el-tag v-else-if="item.feedback_status === 'resolved'" size="small" type="success">已处理</el-tag>
        </div>
        <p>{{ item.answer }}</p>
        <div v-if="item.feedback_reason || item.feedback_note || item.feedback_resolution_note" class="stack-foot">
          <span v-if="item.feedback_reason">原因：{{ feedbackReasonText(item.feedback_reason) }}</span>
          <span v-if="item.feedback_note">备注：{{ item.feedback_note }}</span>
          <span v-if="item.feedback_resolution_note">处理：{{ item.feedback_resolution_note }}</span>
        </div>
        <div v-if="item.citations?.length" class="qa-history-citations">
          <div class="stack-foot">引用来源</div>
          <div class="reference-list">
            <div v-for="(citation, index) in item.citations" :key="`history-citation-${item.id}-${index}`" class="reference-item citation-item">
              <div class="reference-label">
                <el-tag size="small" type="primary">片段 {{ (citation.chunk_index ?? index) + 1 }}</el-tag>
                <strong>{{ citation.section_title || '未标注章节' }}</strong>
                <span class="stack-foot" v-if="citation.page_number">第 {{ citation.page_number }} 页</span>
              </div>
              <blockquote>{{ citation.source_text || '暂无原文片段' }}</blockquote>
            </div>
          </div>
        </div>
        <div class="stack-foot">
          <span>{{ item.model_name || '未知模型' }}</span>
          <span>{{ item.created_at }}</span>
        </div>
      </div>
    </div>
    <el-empty v-else description="暂无问答记录" />
  </el-card>
</template>

<script setup>
import { ElCard } from 'element-plus/es/components/card/index'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/empty/style/css'

const props = defineProps({
  records: { type: Array, default: () => [] },
})
defineEmits(['refresh'])

const REASONS = [
  { label: '答案不准确', value: 'incorrect_answer' },
  { label: '引用不准确', value: 'wrong_citation' },
  { label: '信息不完整', value: 'incomplete_answer' },
  { label: '没有帮助', value: 'not_helpful' },
]

const feedbackValueText = (value) => ({
  positive: '正反馈',
  negative: '负反馈',
}[value] || value || '未反馈')

const feedbackTagType = (value) => ({
  positive: 'success',
  negative: 'danger',
}[value] || 'info')

const feedbackReasonText = (value) => {
  const matched = REASONS.find((item) => item.value === value)
  return matched?.label || value || '未分类'
}
</script>
