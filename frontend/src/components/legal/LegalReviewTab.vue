<template>
  <div class="tab-panel">
    <el-card v-if="reviewStats" shadow="never">
      <template #header><span class="card-title">审核统计</span></template>
      <div class="metrics-grid-inline">
        <div class="stat-mini"><span>总审核动作</span><strong>{{ reviewStats.total_actions }}</strong></div>
        <div class="stat-mini"><span>已通过</span><strong>{{ reviewStats.action_distribution.approve || 0 }}</strong></div>
        <div class="stat-mini"><span>已退回</span><strong>{{ reviewStats.action_distribution.return || 0 }}</strong></div>
        <div class="stat-mini"><span>转线下</span><strong>{{ reviewStats.action_distribution.offline || 0 }}</strong></div>
      </div>
      <el-divider content-position="left">退回原因</el-divider>
      <div v-if="reviewStats.return_reasons?.length" class="return-reason-list">
        <div v-for="(r, i) in reviewStats.return_reasons" :key="i" class="return-reason-item">
          <el-tag size="small" type="warning">{{ targetLabel(r.target_type) }} #{{ r.target_id }}</el-tag>
          <span>{{ r.note }}</span>
        </div>
      </div>
      <el-empty v-else description="暂无退回记录" :image-size="60" />
    </el-card>

    <el-card shadow="never" style="margin-top:20px">
      <template #header><span class="card-title">律师审核队列</span></template>
      <el-table :data="reviewQueue" stripe size="small" row-key="id" @expand-change="onExpandReview">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="review-detail">
              <div class="review-detail-original">
                <strong>原文/内容：</strong>
                <pre class="review-detail-content">{{ row.question || row.content || row.title || '无内容' }}</pre>
              </div>
              <div class="review-detail-history">
                <strong>审核历史：</strong>
                <div v-if="reviewHistoryMap[reviewKey(row)]?.length" class="history-timeline">
                  <div v-for="h in reviewHistoryMap[reviewKey(row)]" :key="h.id" class="history-entry">
                    <el-tag size="small" :type="h.action === 'comment' ? 'info' : statusTagType(h.to_status)">{{ actionLabel(h.action) }}</el-tag>
                    <span v-if="h.action !== 'comment'" class="history-transition">{{ statusLabel(h.from_status) }} → {{ statusLabel(h.to_status) }}</span>
                    <span class="history-time">{{ formatDate(h.created_at) }}</span>
                    <p v-if="h.note" class="history-note">{{ h.note }}</p>
                  </div>
                </div>
                <el-empty v-else description="暂无审核历史" :image-size="48" />

                <div class="comment-box">
                  <el-input
                    v-model="commentDraft[reviewKey(row)]"
                    type="textarea"
                    :rows="2"
                    placeholder="添加批注（不改变审核状态，仅留言沟通）..."
                    maxlength="2000"
                  />
                  <el-button size="small" type="primary" :loading="commentLoading[reviewKey(row)]" @click="submitComment(row)" style="margin-top:8px">发送批注</el-button>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="target_type" label="类型" width="120">
          <template #default="{ row }">
            {{ targetLabel(row.target_type) }}
          </template>
        </el-table-column>
        <el-table-column label="内容" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.question || row.title || row.content?.slice(0, 80) || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280">
          <template #default="{ row }">
            <el-button size="small" type="success" @click="reviewAction(row, 'approve')">通过</el-button>
            <el-button size="small" type="warning" @click="reviewAction(row, 'return')">退回</el-button>
            <el-button size="small" type="info" @click="reviewAction(row, 'offline')">转线下</el-button>
            <el-button size="small" type="danger" @click="reviewAction(row, 'close')">关闭</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDivider } from 'element-plus/es/components/divider/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { legalWorkspace } from '../../api'
import { useLegalReviewQueue } from '../../composables/useLegalReviewQueue'
import {
  actionLabel,
  formatDate,
  statusLabel,
  statusTagType,
  targetLabel,
} from '../../composables/useLegalWorkspacePresentation'

const {
  reviewStats,
  reviewQueue,
  reviewHistoryMap,
  reviewKey,
  onExpandReview,
  submitComment,
  reviewAction,
  commentDraft,
  commentLoading,
  loadReviewQueue,
  loadReviewStats,
} = useLegalReviewQueue({
  client: legalWorkspace,
  message: ElMessage,
  prompt: ElMessageBox.prompt,
  targetLabel,
})

onMounted(async () => {
  await Promise.all([loadReviewQueue(), loadReviewStats()])
})

defineExpose({
  refresh: () => Promise.all([loadReviewQueue(), loadReviewStats()]),
})
</script>

<style scoped>
.tab-panel {
  display: grid;
  gap: 20px;
}
.card-title {
  font-weight: 700;
  font-size: 15px;
}
.metrics-grid-inline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 8px;
}
.stat-mini {
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 10px 14px;
  display: grid;
  gap: 4px;
}
.stat-mini span {
  color: var(--color-text-muted);
  font-size: 12px;
}
.stat-mini strong {
  font-size: 20px;
}
.return-reason-list {
  display: grid;
  gap: 8px;
}
.return-reason-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.review-detail {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  padding: 8px 12px;
}
.review-detail-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.6;
  background: var(--el-fill-color-light);
  padding: 8px;
  border-radius: 4px;
  max-height: 180px;
  overflow-y: auto;
}
.history-timeline {
  display: grid;
  gap: 10px;
}
.history-entry {
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
}
.history-transition,
.history-time {
  margin-left: 8px;
  color: var(--color-text-muted);
}
.history-note {
  margin: 6px 0 0;
  font-size: 12px;
}
.comment-box {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

@media (max-width: 900px) {
  .review-detail {
    grid-template-columns: 1fr;
  }
}
</style>
