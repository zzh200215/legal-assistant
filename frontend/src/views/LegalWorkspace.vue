<template>
  <div class="legal-workspace">
    <div class="legal-banner">
      <el-alert type="warning" :closable="false" show-icon>
        <template #title>
          <span style="font-weight:600">AI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。</span>
        </template>
      </el-alert>
    </div>

    <div class="case-bar">
      <span class="case-label">当前案件</span>
      <el-select v-model="currentCaseId" placeholder="选择案件（咨询/审查/文书将归档到该案件）" clearable filterable class="case-select">
        <el-option v-for="c in cases" :key="c.id" :label="`#${c.id} ${c.title}`" :value="c.id">
          <span>{{ c.title }}</span>
          <span class="case-count">咨询{{ c.item_counts?.consultations ?? 0 }} · 审查{{ c.item_counts?.reviews ?? 0 }} · 文书{{ c.item_counts?.drafts ?? 0 }}</span>
        </el-option>
      </el-select>
      <el-button size="small" type="primary" plain @click="openCaseDialog">新建案件</el-button>
    </div>

    <el-tabs v-model="activeTab" class="legal-tabs">
      <el-tab-pane label="法律咨询" name="consultation">
        <div class="tab-panel">
          <el-card shadow="never">
            <template #header><span class="card-title">法律咨询辅助</span></template>
            <el-form @submit.prevent="submitConsultation">
              <el-form-item label="描述您的法律问题">
                <el-input v-model="consultForm.question" type="textarea" :rows="4" placeholder="例如：我在公司工作了3年，公司突然辞退我，没有支付经济补偿金..." maxlength="12000" show-word-limit />
              </el-form-item>
              <el-button type="primary" :loading="consultLoading" @click="submitConsultation">提交咨询</el-button>
            </el-form>
          </el-card>

          <div v-if="consultResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">咨询结果</span>
                  <el-tag v-if="quotaHint('consultation')" size="small" effect="plain" :type="quotaSummary?.consultation?.remaining <= 0 ? 'danger' : 'warning'">{{ quotaHint('consultation') }}</el-tag>
                  <el-tag :type="riskTagType(consultResult.risk_level)" size="small">{{ riskLabel(consultResult.risk_level) }}</el-tag>
                  <el-tag v-if="consultResult.confidence !== undefined" :type="confidenceTagType(consultResult.confidence)" size="small" effect="plain">置信度 {{ consultResult.confidence }}%</el-tag>
                  <el-button v-if="consultResult" size="small" type="success" plain @click="goToReviewFromConsult" style="margin-left:auto">进入合同审查</el-button>
                  <el-button v-if="consultResult" size="small" type="primary" plain @click="goToDraftFromConsult">生成文书</el-button>
                  <el-button v-if="consultResult.status === 'pending_review' || consultResult.status === 'needs_lawyer_review'" size="small" type="primary" @click="submitConsultForReview">提交律师审核</el-button>
                </div>
              </template>
              <el-descriptions :column="1" border>
                <el-descriptions-item label="问题分类">{{ categoryLabel(consultResult.category) }}</el-descriptions-item>
                <el-descriptions-item label="已知事实">
                  <ul v-if="consultResult.known_facts?.length"><li v-for="f in consultResult.known_facts" :key="f">{{ f }}</li></ul>
                  <span v-else class="muted">暂无</span>
                </el-descriptions-item>
                <el-descriptions-item label="待补充事实">
                  <ul v-if="consultResult.missing_facts?.length"><li v-for="f in consultResult.missing_facts" :key="f" class="missing-item">{{ f }}</li></ul>
                  <el-tag v-else type="success" size="small">无缺失</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="参考依据">
                  <div v-for="r in consultResult.references" :key="r.source_id" class="ref-item">
                    <el-button size="small" link type="primary" @click="openSourceDetail(r)">
                      <span class="ref-title">{{ r.title }}</span>
                    </el-button>
                    <span class="ref-citation">{{ r.citation }}</span>
                    <el-tag v-if="r.status" :type="sourceStatusType(r.status)" size="small" effect="plain">{{ sourceStatusLabel(r.status) }}</el-tag>
                    <el-tag v-if="r.verification" :type="verificationTagType(r.verification)" size="small" effect="plain" class="verification-tag">{{ r.verification.verification_note }}</el-tag>
                    <span v-if="r.version" class="ref-version">版本 {{ r.version }}</span>
                  </div>
                  <span v-if="!consultResult.references?.length" class="muted">暂无</span>
                </el-descriptions-item>
                <el-descriptions-item label="一般建议">{{ consultResult.advice }}</el-descriptions-item>
              </el-descriptions>
              <div class="followup-section">
                <el-input v-model="followupQuestion" placeholder="针对此咨询追问..." :disabled="followupLoading">
                  <template #append>
                    <el-button :loading="followupLoading" @click="submitFollowup">追问</el-button>
                  </template>
                </el-input>
              </div>
              <AiOutputFeedback :target-type="'consultation'" :target-id="consultResult.id" :value="consultResult.feedback_score" @submit="submitConsultFeedback" />
            </el-card>
          </div>

          <el-card v-if="consultations.length" shadow="never" class="history-card">
            <template #header><span class="card-title">历史咨询</span></template>
            <el-table :data="consultations" stripe size="small">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="category" label="分类" width="120">
                <template #default="{ row }">{{ categoryLabel(row.category) }}</template>
              </el-table-column>
              <el-table-column prop="question" label="问题" show-overflow-tooltip />
              <el-table-column prop="risk_level" label="风险" width="80">
                <template #default="{ row }">
                  <el-tag :type="riskTagType(row.risk_level)" size="small">{{ riskLabel(row.risk_level) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="120">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="合同审查" name="contract">
        <div class="tab-panel">
          <el-card shadow="never">
            <template #header><span class="card-title">合同智能审查</span></template>
            <el-form @submit.prevent="submitContractReview">
              <el-form-item label="合同标题">
                <el-input v-model="contractForm.title" placeholder="例如：技术服务合同" />
              </el-form-item>
              <el-form-item label="合同内容">
                <el-input v-model="contractForm.content" type="textarea" :rows="8" placeholder="粘贴合同全文或主要条款..." maxlength="50000" show-word-limit />
              </el-form-item>
              <div class="upload-row">
                <el-upload
                  :show-file-list="false"
                  :before-upload="handleContractUpload"
                  accept=".pdf,.docx,.doc,.txt,.md"
                >
                  <el-button :loading="uploadLoading" icon="Upload">上传合同文件（PDF/DOCX/TXT）</el-button>
                </el-upload>
                <span class="muted">或直接粘贴文本后点击审查</span>
              </div>
              <el-button type="primary" :loading="contractLoading" @click="submitContractReview" style="margin-top:12px">开始审查</el-button>
            </el-form>
          </el-card>

          <div v-if="contractResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">审查意见</span>
                  <el-tag v-if="quotaHint('review')" size="small" effect="plain" :type="quotaSummary?.review?.remaining <= 0 ? 'danger' : 'warning'">{{ quotaHint('review') }}</el-tag>
                  <el-tag :type="contractResult.status === 'needs_lawyer_review' ? 'danger' : 'warning'" size="small">{{ statusLabel(contractResult.status) }}</el-tag>
                  <el-tag v-if="contractResult.confidence !== undefined" :type="confidenceTagType(contractResult.confidence)" size="small" effect="plain">置信度 {{ contractResult.confidence }}%</el-tag>
                  <el-button size="small" @click="exportReview" style="margin-left:auto">导出意见书</el-button>
                </div>
              </template>
              <p class="summary-text">{{ contractResult.summary }}</p>

              <el-divider content-position="left">合同原文</el-divider>
              <div ref="contractContentRef" class="contract-content">
                <pre v-for="(para, idx) in contractParagraphs" :key="idx" :id="`para-${idx + 1}`" class="contract-paragraph" :class="{ highlighted: highlightedParagraph === idx + 1 }">{{ para }}</pre>
              </div>

              <el-divider content-position="left">风险明细</el-divider>
              <div class="filter-row">
                <el-select v-model="riskFilter.clauseType" placeholder="按条款类型筛选" clearable size="small" style="width:160px">
                  <el-option v-for="ct in availableClauseTypes" :key="ct" :label="clauseLabel(ct)" :value="ct" />
                </el-select>
                <el-select v-model="riskFilter.level" placeholder="按风险等级筛选" clearable size="small" style="width:140px">
                  <el-option label="高风险" value="high" />
                  <el-option label="中风险" value="medium" />
                  <el-option label="低风险" value="low" />
                </el-select>
                <el-select v-model="riskFilter.sortBy" placeholder="排序方式" clearable size="small" style="width:140px">
                  <el-option label="按风险等级降序" value="risk_desc" />
                  <el-option label="按段落顺序" value="paragraph_asc" />
                </el-select>
              </div>
              <el-table :data="filteredRisks" stripe size="small" style="margin-top:12px" @row-click="jumpToRisk">
                <el-table-column prop="label" label="条款类型" width="120">
                  <template #default="{ row }">{{ clauseLabel(row.clause_type) || row.label }}</template>
                </el-table-column>
                <el-table-column prop="risk_level" label="风险等级" width="100">
                  <template #default="{ row }">
                    <el-tag :type="riskTagType(row.risk_level)" size="small">{{ riskLabel(row.risk_level) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="description" label="风险说明" show-overflow-tooltip />
                <el-table-column prop="suggestion" label="修改建议" show-overflow-tooltip />
                <el-table-column label="原文定位" width="200">
                  <template #default="{ row }">
                    <el-button v-if="row.source_location?.paragraph" size="small" type="primary" link @click.stop="jumpToRisk(row)">
                      <el-icon><LocationInformation /></el-icon>
                      第 {{ row.source_location.paragraph }} 段
                    </el-button>
                    <span v-else class="muted">—</span>
                  </template>
                </el-table-column>
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'open' ? 'warning' : 'info'" size="small">{{ row.status === 'open' ? '待处理' : '待补充' }}</el-tag>
                  </template>
                </el-table-column>
              </el-table>

              <el-divider content-position="left">参考依据</el-divider>
              <div v-if="contractResult.references?.length" class="reference-list">
                <div v-for="r in contractResult.references" :key="r.source_id" class="ref-item">
                  <el-button size="small" link type="primary" @click="openSourceDetail(r)">
                    <span class="ref-title">{{ r.title }}</span>
                  </el-button>
                  <span class="ref-citation">{{ r.citation }}</span>
                  <el-tag v-if="r.status" :type="sourceStatusType(r.status)" size="small" effect="plain">{{ sourceStatusLabel(r.status) }}</el-tag>
                  <el-tag v-if="r.verification" :type="verificationTagType(r.verification)" size="small" effect="plain" class="verification-tag">{{ r.verification.verification_note }}</el-tag>
                  <span v-if="r.version" class="ref-version">版本 {{ r.version }}</span>
                </div>
              </div>
              <span v-else class="muted">暂无参考依据</span>

              <AiOutputFeedback :target-type="'contract_review'" :target-id="contractResult.id" :value="contractResult.feedback_score" @submit="submitReviewFeedback" />
            </el-card>
          </div>

          <el-card v-if="contractReviews.length" shadow="never" class="history-card">
            <template #header><span class="card-title">历史审查</span></template>
            <div class="filter-row">
              <el-select v-model="reviewFilter.status" placeholder="按状态筛选" clearable size="small" style="width:160px">
                <el-option label="待审核" value="pending_review" />
                <el-option label="需律师审查" value="needs_lawyer_review" />
                <el-option label="退回补充" value="returned_for_facts" />
                <el-option label="律师通过" value="lawyer_approved" />
                <el-option label="转线下" value="offline_handled" />
                <el-option label="已关闭" value="closed" />
              </el-select>
              <el-select v-model="reviewFilter.risk" placeholder="按风险筛选" clearable size="small" style="width:140px">
                <el-option label="高风险" value="high" />
                <el-option label="中风险" value="medium" />
                <el-option label="低风险" value="low" />
              </el-select>
            </div>
            <el-table :data="filteredContractReviews" stripe size="small" style="margin-top:12px" row-key="id" @expand-change="onExpandContractReview">
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="version-panel">
                    <div v-if="row.status === 'returned_for_facts'" class="resubmit-form">
                      <strong>该记录已被退回，可修改后重新提交：</strong>
                      <el-input v-model="resubmitDraftForm[row.id]" type="textarea" :rows="4" placeholder="修改合同内容后重新提交..." style="margin-top:8px" />
                      <el-button size="small" type="primary" :loading="resubmitLoading[row.id]" @click="submitContractResubmit(row)" style="margin-top:8px">重新提交</el-button>
                    </div>
                    <div class="version-history">
                      <strong>历史版本：</strong>
                      <div v-if="contractVersionMap[row.id]?.length" class="version-list">
                        <div v-for="v in contractVersionMap[row.id]" :key="v.id" class="version-entry">
                          <el-tag size="small">v{{ v.version }}</el-tag>
                          <span class="version-time">{{ formatDate(v.created_at) }}</span>
                          <span class="version-status">{{ statusLabel(v.status_at_snapshot) }}</span>
                          <pre class="version-content">{{ v.content }}</pre>
                        </div>
                      </div>
                      <el-empty v-else description="暂无历史版本" :image-size="48" />
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="title" label="合同" show-overflow-tooltip />
              <el-table-column label="版本" width="80">
                <template #default="{ row }">v{{ row.version || 1 }}</template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="120">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card shadow="never" class="history-card">
            <template #header>
              <div class="result-header">
                <span class="card-title">合同冲突核对</span>
                <el-tag size="small" type="info">日期 / 金额 / 责任方 / 交付条件</el-tag>
              </div>
            </template>
            <el-form @submit.prevent="submitCompare">
              <div class="compare-grid">
                <div class="compare-col">
                  <el-form-item label="合同A标题">
                    <el-input v-model="compareForm.title_a" placeholder="例如：技术服务合同" />
                  </el-form-item>
                  <el-form-item label="合同A内容">
                    <el-input v-model="compareForm.content_a" type="textarea" :rows="6" placeholder="粘贴合同A全文或主要条款..." maxlength="50000" />
                  </el-form-item>
                </div>
                <div class="compare-col">
                  <el-form-item label="合同B标题">
                    <el-input v-model="compareForm.title_b" placeholder="例如：补充协议" />
                  </el-form-item>
                  <el-form-item label="合同B内容">
                    <el-input v-model="compareForm.content_b" type="textarea" :rows="6" placeholder="粘贴合同B全文或主要条款..." maxlength="50000" />
                  </el-form-item>
                </div>
              </div>
              <el-button type="primary" :loading="compareLoading" @click="submitCompare">开始核对</el-button>
            </el-form>
          </el-card>

          <div v-if="compareResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">核对结果</span>
                  <el-tag :type="compareResult.conflict_count > 0 ? 'danger' : 'success'" size="small">{{ compareResult.conflict_count }} 项差异</el-tag>
                  <el-button size="small" @click="exportCompare" style="margin-left:auto">导出核对报告</el-button>
                </div>
              </template>
              <p class="summary-text">{{ compareResult.summary }}</p>
              <el-table :data="compareResult.fields" stripe size="small" style="margin-top:16px">
                <el-table-column prop="label" label="字段" width="120" />
                <el-table-column label="合同A" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.value_a }}</template>
                </el-table-column>
                <el-table-column label="合同B" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.value_b }}</template>
                </el-table-column>
                <el-table-column label="差异" width="80">
                  <template #default="{ row }">
                    <el-tag :type="row.conflict ? 'danger' : 'success'" size="small">{{ row.conflict ? '不一致' : '一致' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="级别" width="80">
                  <template #default="{ row }">
                    <el-tag :type="riskTagType(row.severity)" size="small">{{ riskLabel(row.severity) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="note" label="说明" show-overflow-tooltip />
              </el-table>
            </el-card>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="文书草稿" name="draft">
        <div class="tab-panel">
          <el-card shadow="never">
            <template #header><span class="card-title">法律文书草稿</span></template>
            <el-form @submit.prevent="submitDraft">
              <el-form-item label="文书类型">
                <el-select v-model="draftForm.document_type" placeholder="选择文书类型" style="width:100%">
                  <el-option v-for="t in templates" :key="t.key" :label="t.label" :value="t.key" />
                </el-select>
              </el-form-item>
              <el-divider content-position="left">事实字段</el-divider>
              <el-form-item v-for="field in currentDraftFields" :key="field" :label="field" :required="isDraftFieldRequired(field)">
                <el-input v-model="draftForm.fields[field]" :placeholder="isDraftFieldRequired(field) ? `【必填】请输入${field}` : `请输入${field}`" />
                <span v-if="isDraftFieldRequired(field) && !draftForm.fields[field]" class="required-hint">此项为必填，缺失将标记为【待补充】</span>
              </el-form-item>
              <el-button type="primary" :loading="draftLoading" @click="submitDraft">生成草稿</el-button>
            </el-form>
          </el-card>

          <div v-if="draftResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">{{ draftResult.title }}</span>
                  <el-tag v-if="quotaHint('draft')" size="small" effect="plain" :type="quotaSummary?.draft?.remaining <= 0 ? 'danger' : 'warning'">{{ quotaHint('draft') }}</el-tag>
                  <el-tag v-if="draftResult.missing_fields?.length" type="danger" size="small">缺失 {{ draftResult.missing_fields.length }} 项</el-tag>
                  <el-tag v-else type="success" size="small">字段完整</el-tag>
                  <el-tag v-if="draftResult.confidence !== undefined" :type="confidenceTagType(draftResult.confidence)" size="small" effect="plain">置信度 {{ draftResult.confidence }}%</el-tag>
                  <el-button size="small" @click="exportDraft" style="margin-left:auto">导出草稿</el-button>
                </div>
              </template>
              <div v-if="draftResult.missing_fields?.length" class="missing-warn">
                <strong>待补充事实：</strong>
                <el-tag v-for="f in draftResult.missing_fields" :key="f" type="danger" size="small" style="margin:2px 4px">{{ f }}</el-tag>
              </div>
              <el-divider />
              <pre class="draft-content">{{ draftResult.content }}</pre>
              <el-divider content-position="left">参考依据</el-divider>
              <div v-if="draftResult.references?.length" class="reference-list">
                <div v-for="r in draftResult.references" :key="r.source_id" class="ref-item">
                  <el-button size="small" link type="primary" @click="openSourceDetail(r)">
                    <span class="ref-title">{{ r.title }}</span>
                  </el-button>
                  <span class="ref-citation">{{ r.citation }}</span>
                  <el-tag v-if="r.status" :type="sourceStatusType(r.status)" size="small" effect="plain">{{ sourceStatusLabel(r.status) }}</el-tag>
                  <el-tag v-if="r.verification" :type="verificationTagType(r.verification)" size="small" effect="plain" class="verification-tag">{{ r.verification.verification_note }}</el-tag>
                  <span v-if="r.version" class="ref-version">版本 {{ r.version }}</span>
                </div>
              </div>
              <span v-else class="muted">暂无参考依据</span>
              <AiOutputFeedback :target-type="'draft'" :target-id="draftResult.id" :value="draftResult.feedback_score" @submit="submitDraftFeedback" />
            </el-card>
          </div>

          <el-card v-if="drafts.length" shadow="never" class="history-card">
            <template #header><span class="card-title">历史草稿</span></template>
            <el-table :data="drafts" stripe size="small">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="title" label="文书" show-overflow-tooltip />
              <el-table-column prop="status" label="状态" width="120">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="法源管理" name="sources">
        <div class="tab-panel">
          <el-card shadow="never">
            <template #header>
              <div class="result-header">
                <span class="card-title">法源批量导入</span>
                <el-upload
                  :before-upload="handleSourceImport"
                  :show-file-list="false"
                  accept=".csv,.xlsx,.xls"
                  style="margin-left:auto"
                >
                  <el-button type="primary" size="small" :loading="importLoading">
                    <el-icon><Upload /></el-icon>
                    上传 CSV/Excel
                  </el-button>
                </el-upload>
              </div>
            </template>
            <el-alert type="info" :closable="false" show-icon>
              <p><strong>CSV 格式要求：</strong></p>
              <ul style="margin:8px 0 0 20px; padding:0">
                <li>必需列：title（标题）, source_type（类型）, content（内容）</li>
                <li>可选列：citation（引用条款）, jurisdiction（适用地域）, version（版本）, effective_date（生效日期 YYYY-MM-DD）, status（状态）</li>
                <li>source_type 值：statute（法律法规）, case（案例摘要）, template（合同模板）</li>
                <li>status 值：active（当前有效）, inactive（已失效）, pending_update（待更新）</li>
              </ul>
            </el-alert>
            <div v-if="importResult" class="import-result">
              <el-tag :type="importResult.skipped > 0 ? 'warning' : 'success'" size="large">
                导入成功 {{ importResult.imported }} 条，跳过 {{ importResult.skipped }} 条
              </el-tag>
              <div v-if="importResult.errors?.length" style="margin-top:12px">
                <strong>错误详情：</strong>
                <ul style="margin:4px 0 0 20px; color:var(--el-color-danger)">
                  <li v-for="(err, i) in importResult.errors" :key="i">{{ err }}</li>
                </ul>
              </div>
            </div>
          </el-card>

          <el-card shadow="never" style="margin-top:20px">
            <template #header><span class="card-title">检索测试工具</span></template>
            <el-form @submit.prevent="submitRetrievalTest">
              <el-form-item label="测试问题">
                <el-input v-model="retrievalQuestion" placeholder="输入问题，实时查看法源召回排序及评分明细..." @keyup.enter="submitRetrievalTest" />
              </el-form-item>
              <el-button type="primary" :loading="retrievalLoading" @click="submitRetrievalTest">测试召回</el-button>
            </el-form>

            <div v-if="retrievalResult" style="margin-top:16px">
              <p class="summary-text">共 {{ retrievalResult.total_sources }} 条法源，召回 {{ retrievalResult.results.filter(r => r.total_score > 0).length }} 条有效匹配</p>
              <el-table :data="retrievalResult.results" stripe size="small" max-height="400">
                <el-table-column prop="title" label="法源名称" show-overflow-tooltip />
                <el-table-column prop="total_score" label="综合得分" width="90" sortable>
                  <template #default="{ row }">
                    <el-tag :type="row.total_score > 0 ? 'success' : 'info'" size="small">{{ row.total_score }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="评分明细" min-width="280">
                  <template #default="{ row }">
                    <span class="score-breakdown">
                      精确:{{ row.score_breakdown.citation_match }}
                      关键词:{{ row.score_breakdown.keyword_match }}
                      分类:{{ row.score_breakdown.category_match }}
                      覆盖度:{{ row.score_breakdown.query_coverage }}
                      状态:{{ row.score_breakdown.status_weight }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="100">
                  <template #default="{ row }">
                    <el-tag :type="sourceStatusType(row.status)" size="small">{{ sourceStatusLabel(row.status) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="命中关键词" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.matched_keywords.join('、') || '无' }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-card>

          <el-card shadow="never" style="margin-top:20px">
            <template #header>
              <div class="result-header">
                <span class="card-title">法源版本治理</span>
                <el-tag size="small" type="info">失效 / 待更新 / 当前有效</el-tag>
                <el-button size="small" type="primary" @click="openSourceDialog()" style="margin-left:auto">新建法源</el-button>
              </div>
            </template>
            <el-table :data="legalSources" stripe size="small">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="title" label="法源名称" show-overflow-tooltip />
              <el-table-column prop="citation" label="引用条款" show-overflow-tooltip />
              <el-table-column prop="version" label="版本" width="80" />
              <el-table-column label="状态" width="140">
                <template #default="{ row }">
                  <el-select v-model="row.status" size="small" @change="updateSourceStatus(row)">
                    <el-option label="当前有效" value="active" />
                    <el-option label="已失效" value="inactive" />
                    <el-option label="待更新" value="pending_update" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column prop="source_type" label="类型" width="100">
                <template #default="{ row }">{{ sourceTypeLabel(row.source_type) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="120">
                <template #default="{ row }">
                  <el-button size="small" text @click="openSourceDialog(row)">编辑</el-button>
                  <el-button size="small" text type="danger" @click="deleteSourceHandler(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-dialog v-model="sourceDialogVisible" :title="editingSource ? '编辑法源' : '新建法源'" width="680px">
            <el-form :model="sourceForm" label-width="110px" size="small">
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="标题" required>
                    <el-input v-model="sourceForm.title" placeholder="如《劳动合同法》" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="类型" required>
                    <el-select v-model="sourceForm.source_type" style="width:100%">
                      <el-option label="法律法规" value="statute" />
                      <el-option label="司法解释" value="judicial_interpretation" />
                      <el-option label="案例摘要" value="case" />
                      <el-option label="合同模板" value="template" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="发文字号">
                    <el-input v-model="sourceForm.document_number" placeholder="如：主席令第65号" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="发布机关">
                    <el-input v-model="sourceForm.promulgator" placeholder="如：全国人大常委会" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="16">
                <el-col :span="12">
                  <el-form-item label="引用条款">
                    <el-input v-model="sourceForm.citation" placeholder="如：劳动合同法第40条" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item label="管辖地域">
                    <el-input v-model="sourceForm.jurisdiction" placeholder="中国大陆" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="16">
                <el-col :span="8">
                  <el-form-item label="版本">
                    <el-input v-model="sourceForm.version" placeholder="v1" />
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item label="状态">
                    <el-select v-model="sourceForm.status" style="width:100%">
                      <el-option label="当前有效" value="active" />
                      <el-option label="已失效" value="inactive" />
                      <el-option label="待更新" value="pending_update" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item label="领域标签">
                    <el-select v-model="sourceForm.law_areas" multiple collapse-tags style="width:100%" placeholder="选择法律领域">
                      <el-option label="劳动法" value="labor" />
                      <el-option label="合同法" value="contract" />
                      <el-option label="民间借贷" value="lending" />
                      <el-option label="消费维权" value="consumer" />
                      <el-option label="公司法" value="company" />
                      <el-option label="知识产权" value="ip" />
                      <el-option label="民事诉讼法" value="civil_procedure" />
                      <el-option label="行政诉讼" value="administrative" />
                      <el-option label="刑事" value="criminal" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>
              <el-form-item label="关键词">
                <el-input v-model="sourceForm.keywordsInput" placeholder="逗号分隔，如：辞退、经济补偿、解除劳动合同" @change="syncKeywords" />
              </el-form-item>
              <el-form-item label="内容摘要" required>
                <el-input v-model="sourceForm.content" type="textarea" :rows="3" placeholder="法源核心内容摘要/简介..." />
              </el-form-item>
              <el-form-item label="全文">
                <el-input v-model="sourceForm.full_text" type="textarea" :rows="4" placeholder="法规全文（可选），不填时自动使用内容摘要" />
              </el-form-item>
            </el-form>
            <template #footer>
              <el-button @click="sourceDialogVisible = false">取消</el-button>
              <el-button type="primary" :loading="sourceSaving" @click="saveSource">保存</el-button>
            </template>
          </el-dialog>
        </div>
      </el-tab-pane>

      <el-tab-pane label="律师审核" name="review">
        <LegalReviewTab ref="reviewTabRef" />
      </el-tab-pane>

      <el-tab-pane label="计时计费" name="billing" lazy>
        <LegalBilling :org-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>

      <el-tab-pane label="关键日期" name="deadlines" lazy>
        <LegalDeadlines :org-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>

      <el-tab-pane label="合同台账" name="contracts" lazy>
        <LegalContracts :org-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>

      <el-tab-pane label="客户门户" name="portal">
        <LegalPortalTab :organization-id="currentOrgId" :case-id="currentCaseId" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="sourceDetailVisible" title="引用依据核对" width="640px">
      <template v-if="sourceDetail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="法源名称">{{ sourceDetail.title }}</el-descriptions-item>
          <el-descriptions-item label="引用条款">{{ sourceDetail.citation || '—' }}</el-descriptions-item>
          <el-descriptions-item label="版本">
            <span v-if="sourceDetail.version">{{ sourceDetail.version }}</span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>
          <el-descriptions-item label="效力状态">
            <el-tag v-if="sourceDetail.status" :type="sourceStatusType(sourceDetail.status)" size="small">{{ sourceStatusLabel(sourceDetail.status) }}</el-tag>
            <span v-else class="muted">未标注</span>
          </el-descriptions-item>
          <el-descriptions-item label="生效日期">
            <span v-if="sourceDetail.effective_date">{{ sourceDetail.effective_date }}</span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="sourceDetail.jurisdiction" label="适用地域">{{ sourceDetail.jurisdiction }}</el-descriptions-item>
          <el-descriptions-item v-if="sourceDetail.verification?.verification_note" label="核验提示">
            <el-tag :type="verificationTagType(sourceDetail.verification)" size="small" effect="plain">{{ sourceDetail.verification.verification_note }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="sourceDetail.verification?.recommended_source" label="建议引用现行版本">
            <span>{{ sourceDetail.verification.recommended_source.title }}</span>
            <el-tag size="small" type="success" style="margin-left: 6px">{{ sourceDetail.verification.recommended_source.version }}</el-tag>
            <el-button size="small" link type="primary" style="margin-left: 6px" @click="openRecommendedSource(sourceDetail.verification.recommended_source)">查看</el-button>
          </el-descriptions-item>
        </el-descriptions>
      </template>
      <el-divider content-position="left">条文（供核对原文）</el-divider>
      <div v-loading="sourceDetailLoading" class="article-list">
        <template v-if="sourceDetailArticles.length">
          <div v-for="article in sourceDetailArticles" :key="article.id" class="article-entry">
            <strong>{{ article.article_number }}</strong>
            <span v-if="article.title" class="article-title">{{ article.title }}</span>
            <p class="article-content">{{ article.content }}</p>
          </div>
        </template>
        <el-empty v-else-if="!sourceDetailLoading" description="该法源暂无条文明细" :image-size="48" />
      </div>
    </el-dialog>

    <el-dialog v-model="caseDialogVisible" title="新建案件" width="520px">
      <el-form :model="caseForm" label-width="90px" size="small">
        <el-form-item label="案件名称" required>
          <el-input v-model="caseForm.title" placeholder="如：张三 vs XX公司 劳动争议" maxlength="256" />
        </el-form-item>
        <el-form-item label="案件类型">
          <el-select v-model="caseForm.case_type" style="width:100%">
            <el-option label="劳动争议" value="labor_dispute" />
            <el-option label="合同纠纷" value="contract_dispute" />
            <el-option label="民间借贷" value="private_lending" />
            <el-option label="消费纠纷" value="consumer_dispute" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="案情摘要">
          <el-input v-model="caseForm.description" type="textarea" :rows="3" placeholder="简要描述案件背景（AES 加密存储）" maxlength="4000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="caseDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="caseCreating" @click="createCase">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElDivider } from 'element-plus/es/components/divider/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTabs, ElTabPane } from 'element-plus/es/components/tabs/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { LocationInformation, Upload } from '@element-plus/icons-vue'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/upload/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import { legalWorkspace } from '../api'
import { subscription as subscriptionApi } from '../api'
import AiOutputFeedback from '../components/AiOutputFeedback.vue'
import { useLegalConsultations } from '../composables/useLegalConsultations'
import { useContractReviews } from '../composables/useContractReviews'
import { useLegalDrafts } from '../composables/useLegalDrafts'
import { useContractComparison } from '../composables/useContractComparison'
import { useLegalSources } from '../composables/useLegalSources'
import LegalPortalTab from '../components/legal/LegalPortalTab.vue'
import LegalReviewTab from '../components/legal/LegalReviewTab.vue'
import {
  categoryLabel,
  clauseLabel,
  formatDate,
  riskLabel,
  riskTagType,
  sourceStatusLabel,
  sourceStatusType,
  sourceTypeLabel,
  statusLabel,
  statusTagType,
  useContractRiskPresentation,
} from '../composables/useLegalWorkspacePresentation'
import LegalBilling from './LegalBilling.vue'
import LegalDeadlines from './LegalDeadlines.vue'
import LegalContracts from './LegalContracts.vue'

const activeTab = ref('consultation')
const overview = ref(null)
const currentOrgId = ref(1)
const currentCaseId = ref(null)
const reviewTabRef = ref(null)
const cases = ref([])
const caseDialogVisible = ref(false)
const caseCreating = ref(false)
const caseForm = ref({ title: '', case_type: 'labor_dispute', description: '' })

// M-3 B 组：结果卡剩余额度提示（配额来自 /billing/subscriptions/quota）
const quotaSummary = ref(null)

const loadQuota = async () => {
  try {
    const { data } = await subscriptionApi.myQuota()
    quotaSummary.value = data
  } catch (e) {
    // 配额接口不可用时静默降级，不阻塞主流程
    quotaSummary.value = null
  }
}

const quotaHint = (type) => {
  const q = quotaSummary.value?.[type]
  if (!q || q.unlimited) return ''
  if (q.remaining <= 0) return `本月${typeLabel(type)}额度已用尽，升级解锁更多`
  return `本月${typeLabel(type)}剩余 ${q.remaining}/${q.quota}`
}

const typeLabel = (t) => ({ consultation: '咨询', review: '审查', draft: '文书' }[t] || t)

const currentDraftFields = computed(() => draftFieldMap.value[draftForm.value.document_type] || [])

const {
  contractForm,
  contractLoading,
  contractResult,
  contractReviews,
  uploadLoading,
  contractVersionMap,
  resubmitDraftForm,
  resubmitLoading,
  loadContractReviews,
  submitContractReview: runContractReview,
  onExpandContractReview,
  submitContractResubmit,
  handleContractUpload: uploadContractReview,
} = useContractReviews({ client: legalWorkspace, message: ElMessage, caseId: currentCaseId })

const {
  reviewFilter,
  riskFilter,
  highlightedParagraph,
  contractContentRef,
  contractParagraphs,
  availableClauseTypes,
  filteredRisks,
  filteredContractReviews,
  resetRiskFilter,
  jumpToRisk,
} = useContractRiskPresentation({ contractForm, contractResult, contractReviews })

const submitContractReview = () => {
  runContractReview(resetRiskFilter)
  loadQuota()
}
const handleContractUpload = (file) => uploadContractReview(file, resetRiskFilter)

const {
  templates,
  draftForm,
  draftLoading,
  draftResult,
  drafts,
  draftFieldMap,
  loadTemplates,
  setTemplateFields,
  loadDrafts,
  submitDraft: runDraftSubmit,
} = useLegalDrafts({ client: legalWorkspace, message: ElMessage, caseId: currentCaseId })

const submitDraft = () => {
  runDraftSubmit()
  loadQuota()
}

const { compareForm, compareLoading, compareResult, submitCompare } = useContractComparison({
  client: legalWorkspace, message: ElMessage,
})

const {
  legalSources,
  importLoading,
  importResult,
  retrievalQuestion,
  retrievalLoading,
  retrievalResult,
  sourceDialogVisible,
  editingSource,
  sourceSaving,
  sourceForm,
  loadLegalSources,
  handleSourceImport,
  syncKeywords,
  openSourceDialog,
  saveSource,
  deleteSourceHandler,
  updateSourceStatus,
  submitRetrievalTest,
} = useLegalSources({ client: legalWorkspace, message: ElMessage, confirm: ElMessageBox.confirm })

const DRAFT_REQUIRED_KEYWORDS = ['申请人', '被申请人', '原告', '被告', '姓名', '身份', '金额', '日期', '地址', '请求', '证据', '投诉人', '被投诉']
const isDraftFieldRequired = (field) => DRAFT_REQUIRED_KEYWORDS.some((kw) => field.includes(kw))
const loadOverview = async () => {
  try {
    const { data } = await legalWorkspace.getLegalOverview()
    overview.value = data
    currentOrgId.value = data.organization_id || currentOrgId.value
  } catch {}
}

const loadCases = async () => {
  try {
    const { data } = await legalWorkspace.listCases(currentOrgId.value)
    cases.value = data
    if (!currentCaseId.value && data.length) {
      const active = data.find((c) => c.status === 'in_progress') || data[0]
      currentCaseId.value = active.id
    }
  } catch {}
}

const openCaseDialog = () => {
  caseForm.value = { title: '', case_type: 'labor_dispute', description: '' }
  caseDialogVisible.value = true
}

const createCase = async () => {
  if (!caseForm.value.title.trim()) return ElMessage.warning('请输入案件名称')
  caseCreating.value = true
  try {
    const { data } = await legalWorkspace.createCase(currentOrgId.value, {
      ...caseForm.value, organization_id: currentOrgId.value,
    })
    ElMessage.success(`案件 #${data.id} 已创建`)
    caseDialogVisible.value = false
    currentCaseId.value = data.id
    await loadCases()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '创建失败')
  } finally {
    caseCreating.value = false
  }
}

const CATEGORY_TO_DRAFT_TYPE = {
  labor_dispute: 'labor_arbitration_application',
  private_lending: 'private_lending_complaint',
  consumer_dispute: 'consumer_complaint',
}

const goToDraftFromConsult = () => {
  if (!consultResult.value) return
  const type = CATEGORY_TO_DRAFT_TYPE[consultResult.value.category]
  if (type) draftForm.value.document_type = type
  const facts = (consultResult.value.known_facts || []).join('；')
  draftForm.value.fields = { ...(facts ? { 事实与理由: facts } : {}) }
  activeTab.value = 'draft'
  ElMessage.info(type ? '已按咨询分类选择文书类型并带入案情，请补充当事人等必填字段' : '已带入咨询案情，请选择文书类型')
}

const goToReviewFromConsult = () => {
  if (!consultResult.value) return
  contractForm.value.title = `${categoryLabel(consultResult.value.category)}关联审查`
  const facts = (consultResult.value.known_facts || []).join('\n')
  contractForm.value.content = facts
  activeTab.value = 'contract'
  ElMessage.info('已带入咨询案情，请粘贴合同全文后开始审查')
}

const {
  consultForm,
  consultLoading,
  consultResult,
  consultations,
  followupQuestion,
  followupLoading,
  loadConsultations,
  submitConsultation: runConsultation,
  submitFollowup,
  submitConsultForReview,
} = useLegalConsultations({
  client: legalWorkspace,
  message: ElMessage,
  confirm: ElMessageBox.confirm,
  caseId: currentCaseId,
  onReviewSubmitted: () => reviewTabRef.value?.refresh(),
})

const submitConsultation = () => {
  runConsultation()
  loadQuota()
}

const exportCompare = () => {
  if (!compareResult.value) return
  const r = compareResult.value
  const lines = ['# 合同冲突核对报告', '', `**合同A：** ${compareForm.value.title_a || '合同A'}`, `**合同B：** ${compareForm.value.title_b || '合同B'}`, `**差异项：** ${r.conflict_count} 项`, '', '## 核对总结', r.summary || '', '', '## 字段对比明细']
  if (r.fields?.length) {
    r.fields.forEach((item) => {
      const flag = item.conflict ? '⚠️ 不一致' : '✓ 一致'
      lines.push(`### ${item.label}（${flag}，${riskLabel(item.severity)}）`)
      lines.push(`- 合同A：${item.value_a || '未提及'}`)
      lines.push(`- 合同B：${item.value_b || '未提及'}`)
      lines.push(`- 说明：${item.note || '无'}`)
      lines.push('')
    })
  }
  lines.push('---', '*AI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。*')
  downloadText('合同冲突核对报告.md', lines.join('\n'))
}

const exportReview = () => {
  if (!contractResult.value) return
  const r = contractResult.value
  const lines = [`# 合同审查意见书`, ``, `**合同标题：** ${contractForm.value.title || '未命名'}`, `**审查状态：** ${statusLabel(r.status)}`, ``, `## 审查摘要`, r.summary || '', ``, `## 条款风险明细`]
  if (r.risks?.length) {
    r.risks.forEach((item, i) => {
      lines.push(`### ${i + 1}. ${item.label}（${riskLabel(item.risk_level)}）`)
      lines.push(`- 风险说明：${item.description || '无'}`)
      lines.push(`- 修改建议：${item.suggestion || '无'}`)
      lines.push('')
    })
  } else {
    lines.push('未识别到条款风险。')
  }
  lines.push('', '---', '*AI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。*')
  downloadText(`${contractForm.value.title || '合同审查意见书'}.md`, lines.join('\n'))
}

const exportDraft = () => {
  if (!draftResult.value) return
  const d = draftResult.value
  const lines = [`# ${d.title || '法律文书草稿'}`]
  if (d.missing_fields?.length) {
    lines.push('', `**待补充字段：** ${d.missing_fields.join('、')}`)
  }
  lines.push('', '---', d.content || '')
  downloadText(`${d.title || '法律文书草稿'}.md`, lines.join('\n'))
}

const downloadText = (filename, text) => {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const confidenceTagType = (score) => {
  if (score >= 80) return 'success'
  if (score >= 55) return 'warning'
  return 'danger'
}

const verificationTagType = (v) => {
  if (!v || v.verified === false) return 'info'
  if (v.status === 'inactive') return 'danger'
  if (v.superseded || v.status === 'pending_update') return 'warning'
  if (v.current_effective) return 'success'
  return 'info'
}

const submitFeedback = async (kind, targetId, score, note) => {
  const api = {
    consultation: legalWorkspace.submitConsultationFeedback,
    contract_review: legalWorkspace.submitReviewFeedback,
    draft: legalWorkspace.submitDraftFeedback,
  }[kind]
  try {
    await api(targetId, { score, note })
    return true
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '反馈提交失败')
    return false
  }
}

const submitConsultFeedback = async (score, note) => {
  if (!consultResult.value?.id) return
  const ok = await submitFeedback('consultation', consultResult.value.id, score, note)
  if (ok) { consultResult.value.feedback_score = score; ElMessage.success('反馈已提交，感谢您的评价') }
}

const submitReviewFeedback = async (score, note) => {
  if (!contractResult.value?.id) return
  const ok = await submitFeedback('contract_review', contractResult.value.id, score, note)
  if (ok) { contractResult.value.feedback_score = score; ElMessage.success('反馈已提交，感谢您的评价') }
}

const submitDraftFeedback = async (score, note) => {
  if (!draftResult.value?.id) return
  const ok = await submitFeedback('draft', draftResult.value.id, score, note)
  if (ok) { draftResult.value.feedback_score = score; ElMessage.success('反馈已提交，感谢您的评价') }
}

const sourceDetailVisible = ref(false)
const sourceDetail = ref(null)
const sourceDetailArticles = ref([])
const sourceDetailLoading = ref(false)
const openRecommendedSource = async (recommended) => {
  if (!recommended?.source_id) return
  openSourceDetail({ ...recommended, source_id: recommended.source_id })
}

const openSourceDetail = async (refItem) => {
  sourceDetail.value = refItem
  sourceDetailArticles.value = []
  sourceDetailVisible.value = true
  if (!refItem?.source_id) return
  sourceDetailLoading.value = true
  try {
    const { data } = await legalWorkspace.getSourceArticles(refItem.source_id)
    sourceDetailArticles.value = data || []
  } catch {
    sourceDetailArticles.value = []
  } finally {
    sourceDetailLoading.value = false
  }
}

onMounted(async () => {
  await loadOverview()
  await loadCases()
  loadQuota()
  setTemplateFields({
    labor_arbitration_application: ['申请人', '被申请人', '劳动关系起止时间', '仲裁请求', '事实与理由', '证据清单'],
    private_lending_complaint: ['原告', '被告', '借款金额', '借款日期', '诉讼请求', '事实与理由', '证据清单'],
    consumer_complaint: ['投诉人', '被投诉企业', '购买商品或服务', '消费金额与日期', '投诉请求', '事实与理由', '证据清单'],
    supplementary_agreement: ['甲方', '乙方', '原协议名称', '补充事项', '生效日期', '签署地点'],
  })
  loadConsultations()
  loadContractReviews()
  loadTemplates()
  loadDrafts()
  loadLegalSources()
})
</script>

<style scoped>
.legal-workspace {
  max-width: 1200px;
  margin: 0 auto;
}

.legal-banner {
  margin-bottom: 20px;
}

.case-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
  padding: 10px 14px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.case-select {
  width: 340px;
  max-width: 100%;
}

.case-label {
  font-weight: 600;
  font-size: 14px;
  color: var(--color-text);
}

.case-count {
  float: right;
  margin-left: 16px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.legal-tabs :deep(.el-tabs__item) {
  font-size: 15px;
  font-weight: 600;
}

.tab-panel {
  display: grid;
  gap: 20px;
}

.card-title {
  font-weight: 700;
  font-size: 15px;
}

.result-card {
  margin-top: 4px;
}

.result-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.history-card {
  margin-top: 4px;
}

.muted {
  color: var(--color-text-muted);
  font-size: 13px;
}

.missing-item {
  color: var(--el-color-danger);
  font-weight: 500;
}

.ref-item {
  margin-bottom: 6px;
}

.reference-list {
  margin-bottom: 8px;
}

.ref-title {
  font-weight: 600;
  margin-right: 8px;
}

.ref-citation {
  color: var(--color-text-muted);
  font-size: 12px;
}

.ref-version {
  margin-left: 8px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.verification-tag {
  margin-left: 8px;
}

.article-list {
  display: grid;
  gap: 12px;
  max-height: 360px;
  overflow-y: auto;
}

.article-entry {
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
}

.article-title {
  margin-left: 8px;
  color: var(--color-text-secondary);
}

.article-content {
  margin: 6px 0 0;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.summary-text {
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.6;
  margin: 0 0 12px;
}

.missing-warn {
  padding: 12px;
  background: var(--el-color-danger-light-9);
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 13px;
}

.draft-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 14px;
  line-height: 1.8;
  background: var(--el-fill-color-light);
  padding: 20px;
  border-radius: 8px;
  margin: 0;
  font-family: inherit;
}

.compare-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.compare-col {
  min-width: 0;
}

.upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.followup-section {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.filter-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.source-snippet {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.required-hint {
  display: block;
  font-size: 12px;
  color: var(--el-color-danger);
  margin-top: 2px;
}

.contract-content {
  max-height: 400px;
  overflow-y: auto;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}

.contract-paragraph {
  margin: 0 0 12px;
  padding: 8px 12px;
  font-size: 14px;
  line-height: 1.8;
  font-family: inherit;
  white-space: pre-wrap;
  word-break: break-all;
  border-left: 3px solid transparent;
  transition: all 0.3s ease;
  border-radius: 4px;
}

.contract-paragraph.highlighted {
  background: var(--el-color-warning-light-9);
  border-left-color: var(--el-color-warning);
  box-shadow: 0 0 0 3px var(--el-color-warning-light-9);
  animation: highlightPulse 0.6s ease-in-out;
}

@keyframes highlightPulse {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-4px); }
  75% { transform: translateX(4px); }
}

.import-result {
  margin-top: 16px;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  font-size: 13px;
}

.score-breakdown {
  font-size: 12px;
  color: var(--color-text-muted);
  font-family: monospace;
}

.metrics-grid-inline {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.stat-mini {
  display: grid;
  gap: 4px;
  text-align: center;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.stat-mini span {
  font-size: 12px;
  color: var(--color-text-muted);
}

.stat-mini strong {
  font-size: 22px;
  font-weight: 800;
}

.return-reason-list {
  display: grid;
  gap: 8px;
}

.return-reason-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.review-detail {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  padding: 16px;
  background: var(--el-fill-color-light);
}

.review-detail-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  line-height: 1.7;
  background: #fff;
  padding: 12px;
  border-radius: 6px;
  margin: 8px 0 0;
  max-height: 300px;
  overflow-y: auto;
}

.history-timeline {
  margin-top: 8px;
  display: grid;
  gap: 10px;
}

.history-entry {
  background: #fff;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
}

.history-transition {
  margin-left: 8px;
  color: var(--color-text-secondary);
}

.history-time {
  margin-left: 8px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.history-note {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
}

@media (max-width: 900px) {
  .review-detail {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .case-bar {
    flex-direction: column;
    align-items: stretch;
  }
  .case-select {
    width: 100%;
  }
  .result-header {
    gap: 6px;
  }
  .result-header :deep(.el-button + .el-button) {
    margin-left: 0;
  }
  .legal-tabs :deep(.el-tabs__header) {
    margin-bottom: 12px;
  }
  .contract-content pre,
  .draft-content {
    font-size: 12px;
  }
}

.comment-box {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.version-panel {
  padding: 16px;
  background: var(--el-fill-color-light);
  display: grid;
  gap: 16px;
}

.resubmit-form {
  padding: 12px;
  background: var(--el-color-warning-light-9);
  border-radius: 6px;
}

.version-list {
  margin-top: 8px;
  display: grid;
  gap: 10px;
}

.version-entry {
  background: #fff;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 13px;
}

.version-time, .version-status {
  margin-left: 8px;
  color: var(--color-text-muted);
}

.version-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.6;
  background: var(--el-fill-color-light);
  padding: 8px;
  border-radius: 4px;
  margin: 6px 0 0;
  max-height: 150px;
  overflow-y: auto;
}

</style>
