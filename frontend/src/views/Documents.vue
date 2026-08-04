<template>
  <div class="documents-page">
    <div class="page-header">
      <div class="page-heading-copy">
        <span class="section-eyebrow">Legal Knowledge Base</span>
        <h3>法律知识库</h3>
        <p>上传法规、案例、合同模板与文书模板，解析入库后可围绕法律依据进行检索、引用溯源与文档对比。</p>
      </div>
      <div class="upload-console">
        <el-upload
          class="upload-dropzone"
          drag
          multiple
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onFileChange"
        >
          <div class="upload-dropzone-inner">
            <strong>拖拽法律资料到这里</strong>
            <span>支持法规、案例、合同模板、文书模板等文档</span>
          </div>
        </el-upload>
        <div class="upload-console-foot">
          <span class="toolbar-meta">{{ selectedFiles.length ? `已选 ${selectedFiles.length} 份` : '文件不会自动上传，确认后进入解析队列' }}</span>
          <el-button type="primary" :loading="uploading" :disabled="!selectedFiles.length" @click="uploadAndAnalyze">
            {{ selectedFiles.length > 1 ? '批量上传并分析' : '上传并分析' }}
          </el-button>
        </div>
      </div>
    </div>

    <div class="overview-metrics">
      <div class="metric-tile">
        <span>文档总数</span>
        <strong>{{ documentTotal || documents.length }}</strong>
      </div>
      <div class="metric-tile">
        <span>当前风险点</span>
        <strong>{{ analysis?.risks?.length || 0 }}</strong>
      </div>
      <div class="metric-tile">
        <span>待办提取</span>
        <strong>{{ analysis?.todos?.length || 0 }}</strong>
      </div>
      <div class="metric-tile">
        <span>问答记录</span>
        <strong>{{ qaRecords.length }}</strong>
      </div>
    </div>

    <div class="layout-grid">
      <el-card class="sidebar-card">
        <template #header>
          <div class="card-header-inline">
            <div>
              <div class="section-eyebrow">Document Rail</div>
              <span>文档资源栏</span>
            </div>
            <el-button text @click="fetchDocuments">刷新</el-button>
          </div>
        </template>

        <div class="governance-box">
          <div class="compare-title">知识库与权限</div>
          <p class="toolbar-meta" style="margin:0 0 4px">法规、案例与模板入库后自动关联法律咨询和合同审查。</p>
          <el-select v-model="uploadForm.knowledge_base_name" filterable allow-create default-first-option placeholder="知识库名称" style="width: 100%">
            <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.name" />
          </el-select>
          <el-select v-model="uploadForm.classification" placeholder="文档分类" style="width: 100%">
            <el-option v-for="item in classificationOptions" :key="item" :label="item" :value="item" />
          </el-select>
          <el-input v-model="uploadForm.tags" placeholder="标签，使用逗号分隔" />
          <el-select v-model="uploadForm.sensitivity_level" placeholder="敏感级别" style="width: 100%">
            <el-option label="内部" value="internal" />
            <el-option label="受限" value="restricted" />
            <el-option label="机密" value="confidential" />
          </el-select>
          <el-select v-model="uploadForm.permission_scope" placeholder="权限范围" style="width: 100%">
            <el-option label="仅自己" value="private" />
            <el-option label="公开" value="public" />
            <el-option label="限制用户" value="restricted" />
            <el-option label="角色可见" value="role" />
          </el-select>
          <el-input v-model="uploadForm.permission_users" placeholder="用户 ID，逗号分隔" />
          <el-input v-model="uploadForm.permission_roles" placeholder="角色，逗号分隔" />
        </div>

        <div class="filter-box">
          <div class="compare-title">筛选条件</div>
          <el-select v-model="filters.knowledge_base_id" clearable placeholder="知识库筛选" style="width: 100%" @change="handleFilterChange">
            <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-select v-model="filters.classification" clearable placeholder="分类筛选" style="width: 100%" @change="handleFilterChange">
            <el-option v-for="item in classificationOptions" :key="item" :label="item" :value="item" />
          </el-select>
          <el-select v-model="filters.connector_id" clearable placeholder="来源连接器" style="width: 100%" @change="handleFilterChange">
            <el-option v-for="item in connectors" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-select v-model="filters.sensitivity_level" clearable placeholder="敏感级别" style="width: 100%" @change="handleFilterChange">
            <el-option label="内部" value="internal" />
            <el-option label="受限" value="restricted" />
            <el-option label="机密" value="confidential" />
          </el-select>
          <el-input v-model="filters.q" placeholder="标题搜索" @change="handleFilterChange" />
        </div>

        <div class="retrieval-box">
          <div class="compare-title">向量检索参数</div>
          <div class="retrieval-form-grid">
            <label>
              <span>Top K</span>
              <el-input v-model="retrievalForm.topK" placeholder="8" />
            </label>
            <label>
              <span>Rerank Top N</span>
              <el-input v-model="retrievalForm.rerankTopN" placeholder="5" />
            </label>
          </div>
          <el-select v-model="retrievalForm.rewriteMode" placeholder="Query Rewrite" style="width: 100%">
            <el-option label="自动改写" value="auto" />
            <el-option label="保留原问" value="off" />
          </el-select>
          <el-select v-model="retrievalForm.contextExpand" placeholder="邻近 Chunk 扩展" style="width: 100%">
            <el-option label="前后各 1 段" value="1" />
            <el-option label="前后各 2 段" value="2" />
            <el-option label="不扩展" value="0" />
          </el-select>
          <p>当前问答接口保持原有调用方式，参数用于呈现检索策略配置。</p>
        </div>

          <div v-if="docMeta" class="current-doc">
          <div class="current-doc-head">
            <div class="doc-title">{{ docMeta.title }}</div>
            <el-button size="small" type="primary" plain :loading="downloading" @click="downloadCurrentDocument">
              下载原文
            </el-button>
          </div>
          <div class="doc-tags">
            <el-tag type="primary">{{ docMeta.file_type }}</el-tag>
            <el-tag>{{ docMeta.status }}</el-tag>
            <el-tag v-if="docMeta.version_number">v{{ docMeta.version_number }}</el-tag>
            <el-tag v-if="docMeta.classification" type="success">{{ docMeta.classification }}</el-tag>
            <el-tag v-if="docMeta.connector_name" type="warning">{{ docMeta.connector_name }}</el-tag>
            <el-tag v-if="docMeta.sensitivity_level" :type="docMeta.sensitivity_level === 'confidential' ? 'danger' : docMeta.sensitivity_level === 'restricted' ? 'warning' : 'info'">
              {{ docMeta.sensitivity_level }}
            </el-tag>
          </div>
          <div v-if="documentSourceMeta.hasSource" class="doc-source-meta">
            <span v-if="documentSourceMeta.connectorName"><strong>来源连接器：</strong>{{ documentSourceMeta.connectorName }}</span>
            <span v-if="documentSourceMeta.sourcePath"><strong>来源路径：</strong>{{ documentSourceMeta.sourcePath }}</span>
            <span v-if="documentSourceMeta.originFile"><strong>原始文件：</strong>{{ documentSourceMeta.originFile }}</span>
            <span v-if="documentSourceMeta.syncJobId">
              <strong>同步任务：</strong>
              <el-button text type="primary" size="small" @click="openDocumentSourceSyncJob">
                #{{ documentSourceMeta.syncJobId }}
              </el-button>
            </span>
          </div>
          <div class="download-governance">
            <div class="download-governance-copy">
              <strong>受控下载</strong>
              <span>下载时重新校验权限；可处理格式会生成带下载人标识的副本。</span>
            </div>
            <div class="download-policy-controls">
              <label>
                <span>允许下载</span>
                <el-switch
                  :model-value="docMeta.download_enabled !== false"
                  :loading="downloadPolicySaving"
                  @change="(value) => updateDownloadPolicy('download_enabled', value)"
                />
              </label>
              <label>
                <span>下载水印</span>
                <el-switch
                  :model-value="Boolean(docMeta.watermark_required)"
                  :disabled="docMeta.download_enabled === false"
                  :loading="downloadPolicySaving"
                  @change="(value) => updateDownloadPolicy('watermark_required', value)"
                />
              </label>
            </div>
          </div>
        </div>

        <div class="compare-box">
          <div class="compare-title">多文档对比</div>
          <el-select
            v-model="compareSelection"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择 2 份以上文档"
            style="width: 100%"
          >
            <el-option
              v-for="item in documents"
              :key="item.id"
              :label="item.title"
              :value="item.id"
            />
          </el-select>
          <el-button
            class="compare-button"
            type="warning"
            :loading="compareLoading"
            :disabled="compareSelection.length < 2"
            @click="runCompare"
          >
            生成对比
          </el-button>
        </div>

        <div class="list-section-head">
          <strong>文档列表</strong>
          <span>{{ documentTotal || documents.length }} 份</span>
        </div>

        <div class="doc-list">
          <button
            v-for="item in documents"
            :key="item.id"
            class="doc-item"
            :class="{ active: item.id === docId }"
            @click="selectDocument(item)"
          >
            <strong>{{ item.title }}</strong>
            <span>{{ item.file_type }} · {{ item.status }}<template v-if="item.version_number"> · v{{ item.version_number }}</template></span>
            <div class="doc-item-foot">
              <span>{{ item.classification || '未分类' }}</span>
              <span>{{ item.connector_name || '手动上传' }}</span>
            </div>
          </button>
        </div>

        <el-pagination
          small
          background
          layout="prev, pager, next"
          :current-page="documentPage"
          :page-size="documentPageSize"
          :total="documentTotal"
          class="app-pagination-end"
          @current-change="handleDocumentPageChange"
        />
      </el-card>

      <div class="main-column">
        <el-card class="toolbar-card">
          <template #header>
            <div class="card-header-inline">
              <div>
                <div class="section-eyebrow">Analysis</div>
                <span>单文档分析工作区</span>
              </div>
              <el-space>
                <el-button type="primary" plain :disabled="!docId" @click="openAgentDemo">
                  发送到 Agent 演示
                </el-button>
                <el-button :disabled="!docId" :loading="loading" @click="runAnalysis">重新分析</el-button>
                <el-button type="warning" :disabled="!docId" :loading="creatingTasks" @click="createTasks">
                  提取待办并创建任务
                </el-button>
              </el-space>
            </div>
          </template>
          <div class="toolbar-meta">
            <span v-if="docId">当前文档 ID: {{ docId }}</span>
            <span v-else>先上传或选择一份文档</span>
          </div>
          <div v-if="analysisTask.taskId" class="async-status app-state-banner">
            <StatusTag kind="async" :status="analysisTask.state" />
            <span>{{ analysisTaskMessage }}</span>
          </div>
        </el-card>

        <div v-if="docId" class="workspace-strip">
          <div class="workspace-tile">
            <span>当前文档</span>
            <strong>{{ docMeta?.title || `文档 ${docId}` }}</strong>
            <p>{{ docMeta?.classification || '未分类' }} · {{ docMeta?.permission_scope || '未设权限' }}</p>
          </div>
          <div class="workspace-tile">
            <span>问答与引用</span>
            <strong>{{ qaRecords.length }}</strong>
            <p>累计问答记录，支持引用溯源与反馈闭环</p>
          </div>
          <div class="workspace-tile">
            <span>解析与版本</span>
            <strong>{{ parseJobs.length }} / {{ versions.length }}</strong>
            <p>后台任务数 / 当前版本记录</p>
          </div>
        </div>

        <el-card v-if="docId" class="panel-card">
          <template #header>
            <div class="card-header-inline">
              <span>文档问答</span>
              <el-tag size="small" type="info">带引用回答</el-tag>
            </div>
          </template>
          <div class="qa-compose">
            <el-input
              v-model="qaQuestion"
              type="textarea"
              :rows="3"
              :maxlength="500"
              show-word-limit
              placeholder="例如：该条款的法律依据是什么？违约责任如何界定？适用哪条司法解释？"
            />
            <div class="qa-compose-actions">
              <span class="toolbar-meta">提问法律依据、条款适用、风险识别等问题时，系统会优先返回引用片段。</span>
              <el-button type="primary" :loading="asking" :disabled="!qaQuestion.trim()" @click="askDocumentQuestion">
                开始提问
              </el-button>
            </div>
          </div>

          <div v-if="qaResult" class="qa-result">
            <div class="qa-result-top">
              <strong>当前回答</strong>
              <el-tag :type="qaResult.can_answer ? 'success' : 'warning'" size="small">
                {{ qaResult.can_answer ? '可回答' : '无法确认' }}
              </el-tag>
              <el-tag size="small" type="info">置信度 {{ Math.round((qaResult.confidence || 0) * 100) }}%</el-tag>
              <el-tag v-if="qaResult.feedback_value" size="small" :type="feedbackTagType(qaResult.feedback_value)">
                {{ feedbackValueText(qaResult.feedback_value) }}
              </el-tag>
            </div>
            <div class="rich-text">{{ qaResult.answer || '暂无回答' }}</div>

            <div class="qa-feedback-actions">
              <template v-if="qaResult.qa_record_id">
                <el-button
                  size="small"
                  :type="qaResult.feedback_value === 'positive' ? 'success' : 'default'"
                  :loading="submittingFeedback"
                  @click="submitPositiveFeedback"
                >
                  有帮助
                </el-button>
                <el-button
                  size="small"
                  :type="qaResult.feedback_value === 'negative' ? 'danger' : 'default'"
                  :loading="submittingFeedback"
                  @click="openNegativeFeedback"
                >
                  有问题
                </el-button>
              </template>
            </div>

            <div v-if="negativeFeedbackVisible" class="qa-feedback-form">
              <el-select v-model="feedbackForm.feedback_reason" placeholder="选择问题类型" style="width: 180px">
                <el-option v-for="item in feedbackReasonOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
              <el-input
                v-model="feedbackForm.feedback_note"
                type="textarea"
                :rows="2"
                :maxlength="300"
                show-word-limit
                placeholder="补充说明问题"
              />
              <div class="qa-feedback-actions">
                <el-button size="small" @click="cancelNegativeFeedback">取消</el-button>
                <el-button size="small" type="danger" :loading="submittingFeedback" @click="submitNegativeFeedback">
                  提交反馈
                </el-button>
              </div>
            </div>

            <div v-if="qaResult.citations?.length" class="qa-citations">
              <div class="panel-title">引用来源</div>
              <div class="reference-list">
                <div v-for="(item, index) in qaResult.citations" :key="`qa-ref-${index}`" class="reference-item citation-item">
                  <div class="reference-label">
                    <el-tag size="small" type="primary">片段 {{ (item.chunk_index ?? index) + 1 }}</el-tag>
                    <strong>{{ item.section_title || '未标注章节' }}</strong>
                    <span class="stack-foot" v-if="item.page_number">第 {{ item.page_number }} 页</span>
                  </div>
                  <blockquote>{{ item.source_text || '暂无引用原文' }}</blockquote>
                </div>
              </div>
            </div>
          </div>
        </el-card>

        <el-card v-if="docId" class="panel-card">
          <template #header>
            <div class="card-header-inline">
              <span>关联 Agent 执行</span>
              <el-tag size="small" type="warning">{{ relatedAgentRuns.length }}</el-tag>
            </div>
          </template>
          <div v-if="relatedAgentRuns.length" class="stack-list">
            <div v-for="item in relatedAgentRuns" :key="`doc-agent-${item.id}`" class="stack-item">
              <div class="stack-top">
                <strong>#{{ item.id }} {{ item.goal }}</strong>
                <StatusTag kind="agent" :status="item.status" size="small" />
              </div>
              <div class="stack-foot">
                <span>{{ item.created_at }}</span>
                <span>步数：{{ item.total_steps || 0 }}</span>
              </div>
              <el-button size="small" text type="primary" @click="openAgentRun(item.id)">查看执行</el-button>
            </div>
          </div>
          <el-empty v-else description="暂无关联 Agent 执行记录" />
        </el-card>

        <DocumentJobsPanel :parse-jobs="parseJobs" @retry="retryParse" @refresh="fetchParseJobs" />

        <DocumentVersionsPanel v-if="docId" :versions="versions" @refresh="fetchVersions" />

        <DocumentQaHistoryPanel v-if="docId" :records="qaRecords" @refresh="fetchQaRecords" />


        <div v-if="analysis" class="content-section-head">
          <div>
            <div class="section-eyebrow">Insight Output</div>
            <h4>分析结果</h4>
          </div>
          <span>摘要、风险、待办、条款与结构化字段统一查看</span>
        </div>

        <DocumentAnalysisPanels v-if="analysis" :analysis="analysis" :doc-title="docMeta?.title" :doc-id-label="String(docId || '')" />

        <div v-if="compareResult" class="content-section-head">
          <div>
            <div class="section-eyebrow">Compare</div>
            <h4>多文档对比</h4>
          </div>
          <span>用于校验条款差异、风险偏差和动作建议</span>
        </div>

        <el-card v-if="compareResult" class="compare-result-card">
          <template #header>
            <div class="card-header-inline">
              <span>多文档对比结果</span>
              <el-tag type="warning" size="small">{{ compareResult.documents.length }} 份文档</el-tag>
            </div>
          </template>

          <div class="compare-hero">
            <div class="compare-hero-copy">
              <span class="summary-label">整体结论</span>
              <strong>{{ compareResult.documents.map((item) => item.title).join(' / ') }}</strong>
              <p>{{ compareResult.comparison.overview || '暂无结论' }}</p>
            </div>
            <div class="compare-hero-metrics">
              <div class="compare-metric">
                <span>共同点</span>
                <strong>{{ (compareResult.comparison.common_points || []).length }}</strong>
              </div>
              <div class="compare-metric">
                <span>风险差异</span>
                <strong>{{ (compareResult.comparison.risk_delta || []).length }}</strong>
              </div>
              <div class="compare-metric">
                <span>差异项</span>
                <strong>{{ (compareResult.comparison.differences || []).length }}</strong>
              </div>
              <div class="compare-metric">
                <span>建议动作</span>
                <strong>{{ (compareResult.comparison.action_suggestions || []).length }}</strong>
              </div>
              <div class="compare-metric conflict-metric">
                <span>证据充分的冲突</span>
                <strong>{{ compareResult.comparison.conflict_analysis?.confirmed_conflict_count || 0 }}</strong>
              </div>
            </div>
          </div>

          <div class="compare-overview">
            <h4>整体结论</h4>
            <p>{{ compareResult.comparison.overview || '暂无结论' }}</p>
          </div>

          <div class="compare-grid">
            <el-card class="mini-card conflict-card" shadow="never">
              <template #header>
                <div class="card-header-inline">
                  <span>事实冲突核对</span>
                  <el-tag type="danger" size="small">{{ compareResult.comparison.conflict_analysis?.conflicts?.length || 0 }} 条</el-tag>
                </div>
              </template>
              <div v-if="(compareResult.comparison.conflict_analysis?.conflicts || []).length" class="stack-list">
                <div v-for="(item, index) in compareResult.comparison.conflict_analysis.conflicts" :key="`conflict-${index}`" class="stack-item conflict-item">
                  <div class="stack-top">
                    <strong>{{ item.field_label }}：{{ item.field }}</strong>
                    <el-tag :type="item.evidence_complete ? severityTagType(item.severity) : 'warning'" size="small">
                      {{ item.evidence_complete ? severityText(item.severity) : '待补证据' }}
                    </el-tag>
                  </div>
                  <div class="conflict-source"><b>{{ item.source_a.document_title }}</b><span>{{ item.source_a.value }}</span><small>{{ item.source_a.source_text || '未定位原文' }}</small><small v-if="item.source_a.page_number || item.source_a.section_title">{{ item.source_a.page_number ? `第 ${item.source_a.page_number} 页` : '' }}{{ item.source_a.section_title ? ` ${item.source_a.section_title}` : '' }}</small></div>
                  <div class="conflict-source"><b>{{ item.source_b.document_title }}</b><span>{{ item.source_b.value }}</span><small>{{ item.source_b.source_text || '未定位原文' }}</small><small v-if="item.source_b.page_number || item.source_b.section_title">{{ item.source_b.page_number ? `第 ${item.source_b.page_number} 页` : '' }}{{ item.source_b.section_title ? ` ${item.source_b.section_title}` : '' }}</small></div>
                  <p>建议：{{ item.recommended_action }}</p>
                </div>
              </div>
              <div v-if="confirmedConflictCount" class="card-actions conflict-actions">
                <el-button type="danger" plain :loading="conflictSuggestionLoading" @click="createConflictSuggestions">生成 {{ confirmedConflictCount }} 项风险任务建议</el-button>
              </div>
              <div v-if="conflictCases.length" class="conflict-case-list">
                <div v-for="item in conflictCases" :key="`conflict-case-${item.id}`" class="conflict-case-row">
                  <div><strong>案例 #{{ item.id }}</strong><span>{{ item.conflict?.field_label }}：{{ item.conflict?.field }}</span></div>
                  <el-tag size="small" :type="conflictCaseTag(item.status)">{{ conflictCaseText(item.status) }}</el-tag>
                  <el-button v-if="item.status === 'pending_confirmation'" size="small" type="danger" plain @click="confirmConflictTask(item)">确认并创建任务</el-button>
                  <el-button v-else-if="item.task_id" size="small" text type="primary" @click="openTask(item.task_id)">查看任务 #{{ item.task_id }}</el-button>
                </div>
              </div>
              <el-empty v-else description="未发现可证实的日期、金额或负责人冲突" />
            </el-card>

            <el-card class="mini-card" shadow="never">
              <template #header>共同点</template>
              <ul class="simple-list">
                <li v-for="(item, index) in compareResult.comparison.common_points || []" :key="`common-${index}`">
                  {{ item }}
                </li>
              </ul>
              <el-empty v-if="!(compareResult.comparison.common_points || []).length" description="暂无共同点" />
            </el-card>

            <el-card class="mini-card" shadow="never">
              <template #header>风险差异</template>
              <div v-if="(compareResult.comparison.risk_delta || []).length" class="stack-list">
                <div v-for="(item, index) in compareResult.comparison.risk_delta" :key="`risk-delta-${index}`" class="stack-item risk-item">
                  <div class="stack-top">
                    <strong>{{ item.title || `风险差异 ${index + 1}` }}</strong>
                    <el-tag v-if="item.severity" :type="severityTagType(item.severity)">{{ severityText(item.severity) }}</el-tag>
                  </div>
                  <p>{{ item.detail || '暂无说明' }}</p>
                </div>
              </div>
              <el-empty v-if="!(compareResult.comparison.risk_delta || []).length" description="暂无风险差异" />
            </el-card>
          </div>

          <el-card class="mini-card" shadow="never">
            <template #header>文档概览</template>
            <el-table :data="compareResult.summary_cards || []" border size="small">
              <el-table-column prop="title" label="文档" min-width="180" />
              <el-table-column prop="risk_count" label="风险数" width="90" />
              <el-table-column prop="todo_count" label="待办数" width="90" />
              <el-table-column prop="reference_count" label="引用数" width="90" />
            </el-table>
          </el-card>

          <div class="compare-grid">
            <el-card class="mini-card" shadow="never">
              <template #header>差异项</template>
              <div v-if="(compareResult.comparison.differences || []).length" class="stack-list">
                <div v-for="(item, index) in compareResult.comparison.differences" :key="`diff-${index}`" class="stack-item">
                  <div class="stack-top">
                    <strong>{{ item.title || `差异 ${index + 1}` }}</strong>
                  </div>
                  <p>{{ item.detail || '暂无说明' }}</p>
                </div>
              </div>
              <el-empty v-else description="暂无明显差异" />
            </el-card>

            <el-card class="mini-card" shadow="never">
              <template #header>建议动作</template>
              <ul class="simple-list">
                <li
                  v-for="(item, index) in compareResult.comparison.action_suggestions || []"
                  :key="`suggest-${index}`"
                >
                  {{ item }}
                </li>
              </ul>
              <el-empty v-if="!(compareResult.comparison.action_suggestions || []).length" description="暂无建议动作" />
            </el-card>
          </div>
        </el-card>

        <div v-if="createdTasks.length" class="content-section-head">
          <div>
            <div class="section-eyebrow">Task Output</div>
            <h4>任务产出</h4>
          </div>
          <span>从文档待办直接生成任务并进入任务中心</span>
        </div>

        <el-card v-if="createdTasks.length" class="task-card">
          <template #header>
              <div class="card-header-inline">
                <span>已创建任务</span>
                <el-tag type="success" size="small">{{ createdTasks.length }}</el-tag>
                <el-button size="small" text type="primary" @click="openDocumentTasks">查看文档任务</el-button>
              </div>
            </template>
          <el-table :data="createdTasks" border size="small">
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="title" label="标题" min-width="180" />
            <el-table-column prop="assignee" label="负责人" width="120" />
            <el-table-column prop="priority" label="优先级" width="100" />
            <el-table-column prop="status" label="状态" width="110" />
            <el-table-column label="操作" width="110">
              <template #default="{ row }">
                <el-button text type="primary" @click="openTask(row.id)">查看任务</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElSpace } from 'element-plus/es/components/space/index'
import { ElSwitch } from 'element-plus/es/components/switch/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/space/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/upload/style/css'
import api from '../api'
import StatusTag from '../components/StatusTag.vue'
import DocumentAnalysisPanels from '../components/documents/DocumentAnalysisPanels.vue'
import DocumentJobsPanel from '../components/documents/DocumentJobsPanel.vue'
import DocumentVersionsPanel from '../components/documents/DocumentVersionsPanel.vue'
import DocumentQaHistoryPanel from '../components/documents/DocumentQaHistoryPanel.vue'
import { buildAgentDemoRouteQuery } from '../utils/agentDemo'
import { useDocumentQaFeedback } from '../composables/useDocumentQaFeedback'
import {
  documentConflictCaseTag as conflictCaseTag,
  documentConflictCaseText as conflictCaseText,
  documentReferenceTagType as referenceTagType,
  documentReferenceTypeText as referenceTypeText,
  documentSeverityTagType as severityTagType,
  documentSeverityText as severityText,
} from '../utils/workspacePresentation'

const route = useRoute()
const router = useRouter()
let analysisPollingTimer = null
const file = ref(null)
const selectedFiles = ref([])
const uploading = ref(false)
const loading = ref(false)
const compareLoading = ref(false)
const creatingTasks = ref(false)
const downloading = ref(false)
const downloadPolicySaving = ref(false)
const docId = ref(null)
const docMeta = ref(null)
const documents = ref([])
const knowledgeBases = ref([])
const connectors = ref([])
const versions = ref([])
const documentPage = ref(1)
const documentPageSize = ref(10)
const documentTotal = ref(0)
const analysis = ref(null)
const compareSelection = ref([])
const compareResult = ref(null)
const conflictCases = ref([])
const conflictSuggestionLoading = ref(false)
const createdTasks = ref([])
const relatedAgentRuns = ref([])

const confirmedConflictCount = computed(() => (compareResult.value?.comparison?.conflict_analysis?.conflicts || []).filter((item) => item.evidence_complete).length)
const parseJobs = ref([])
const qaRecords = ref([])
const analysisTask = ref({
  taskId: null,
  state: '',
  message: '',
})
const uploadForm = ref({
  knowledge_base_name: '',
  classification: '',
  tags: '',
  sensitivity_level: 'internal',
  permission_scope: 'private',
  permission_users: '',
  permission_roles: '',
})
const filters = ref({
  knowledge_base_id: null,
  classification: '',
  connector_id: route.query.connectorId ? Number(route.query.connectorId) : null,
  sensitivity_level: '',
  q: '',
})
const retrievalForm = ref({
  topK: '8',
  rerankTopN: '5',
  rewriteMode: 'auto',
  contextExpand: '1',
})
const classificationOptions = ['statute', 'judicial_interpretation', 'case_summary', 'contract_template', 'draft_template', 'regulation', 'general']

const feedbackReasonOptions = [
  { label: '答案不准确', value: 'incorrect_answer' },
  { label: '引用不准确', value: 'wrong_citation' },
  { label: '信息不完整', value: 'incomplete_answer' },
  { label: '没有帮助', value: 'not_helpful' },
]

const {
  qaQuestion,
  qaResult,
  asking,
  submittingFeedback,
  negativeFeedbackVisible,
  feedbackForm,
  resetFeedbackForm,
  askDocumentQuestion,
  submitPositiveFeedback,
  openNegativeFeedback,
  cancelNegativeFeedback,
  submitNegativeFeedback,
} = useDocumentQaFeedback({ client: api, message: ElMessage, documentId: docId, refreshRecords: () => fetchQaRecords() })

const normalizeConnectorId = (value) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

const emptyAnalysis = () => ({
  summary: '',
  risks: [],
  todos: [],
  clauses: [],
  structured_fields: {
    dates: [],
    amounts: [],
    owners: [],
    risk_clauses: [],
  },
  references: [],
})

const normalizeStructuredFields = (value) => ({
  dates: value?.dates || [],
  amounts: value?.amounts || [],
  owners: value?.owners || [],
  risk_clauses: value?.risk_clauses || [],
})

const structuredFieldCount = (value) => {
  if (!value) return 0
  return ['dates', 'amounts', 'owners', 'risk_clauses'].reduce((total, key) => total + ((value[key] || []).length), 0)
}

const analysisTaskMessage = computed(() => analysisTask.value.message || '文档分析正在后台执行')

const parseDocumentMetadata = (value) => {
  if (!value) return {}
  if (typeof value === 'object') return value
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

const documentSourceMeta = computed(() => {
  const metadata = parseDocumentMetadata(docMeta.value?.metadata_json)
  return {
    hasSource: Boolean(
      docMeta.value?.connector_name ||
      metadata.connector_name ||
      metadata.connector_source_path ||
      metadata.connector_origin_file ||
      metadata.connector_sync_job_id
    ),
    connectorName: docMeta.value?.connector_name || metadata.connector_name || '',
    sourcePath: metadata.connector_source_path || '',
    originFile: metadata.connector_origin_file || '',
    syncJobId: metadata.connector_sync_job_id || null,
  }
})

const onFileChange = (_uploadFile, uploadFiles) => {
  selectedFiles.value = (uploadFiles || []).map((item) => item.raw).filter(Boolean)
  file.value = selectedFiles.value[0] || null
}

const parseJsonArray = (value) => {
  if (!value) return []
  if (Array.isArray(value)) return value
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

const feedbackValueText = (value) => ({
  positive: '正反馈',
  negative: '负反馈',
}[value] || value || '未反馈')

const feedbackTagType = (value) => ({
  positive: 'success',
  negative: 'danger',
}[value] || 'info')

const fetchDocuments = async () => {
  try {
      const { data } = await api.listDocuments({
        page: documentPage.value,
        page_size: documentPageSize.value,
        knowledge_base_id: filters.value.knowledge_base_id || undefined,
        classification: filters.value.classification || undefined,
        connector_id: filters.value.connector_id || undefined,
        sensitivity_level: filters.value.sensitivity_level || undefined,
        q: filters.value.q || undefined,
      })
    documents.value = data?.items || []
    documentTotal.value = data?.total || 0
  } catch {
    documents.value = []
    documentTotal.value = 0
  }
}

const handleDocumentPageChange = async (page) => {
  documentPage.value = page
  await fetchDocuments()
}

const handleFilterChange = async () => {
  documentPage.value = 1
  const nextQuery = { ...route.query }
  if (filters.value.connector_id) {
    nextQuery.connectorId = String(filters.value.connector_id)
  } else {
    delete nextQuery.connectorId
  }
  router.replace({ query: nextQuery })
  await fetchDocuments()
}

const fetchKnowledgeBases = async () => {
  try {
    const { data } = await api.listKnowledgeBases()
    knowledgeBases.value = data || []
  } catch {
    knowledgeBases.value = []
  }
}

const fetchConnectors = async () => {
  try {
    const { data } = await api.listConnectors()
    connectors.value = data || []
  } catch {
    connectors.value = []
  }
}

const fetchParseJobs = async () => {
  if (!docId.value) {
    parseJobs.value = []
    return
  }
  try {
    const { data } = await api.listDocumentParseJobs(docId.value)
    parseJobs.value = data?.items || []
  } catch {
    parseJobs.value = []
  }
}

const fetchVersions = async () => {
  if (!docId.value) {
    versions.value = []
    return
  }
  try {
    const { data } = await api.listDocumentVersions(docId.value)
    versions.value = data?.items || []
  } catch {
    versions.value = []
  }
}

const fetchQaRecords = async () => {
  if (!docId.value) {
    qaRecords.value = []
    return
  }
  try {
    const { data } = await api.listDocumentQaRecords(docId.value)
    qaRecords.value = ((data?.items) || []).map((item) => ({
      ...item,
      citations: parseJsonArray(item.citations),
    }))
  } catch {
    qaRecords.value = []
  }
}

const replaceDocumentQuery = (documentId) => {
  const nextQuery = { ...route.query }
  if (documentId) {
    nextQuery.documentId = String(documentId)
  } else {
    delete nextQuery.documentId
  }
  router.replace({ query: nextQuery })
}

const clearAnalysisPolling = () => {
  if (analysisPollingTimer) {
    clearTimeout(analysisPollingTimer)
    analysisPollingTimer = null
  }
}

const resetAnalysisTask = () => {
  clearAnalysisPolling()
  analysisTask.value = {
    taskId: null,
    state: '',
    message: '',
  }
}

const openTask = (taskId) => {
  router.push({ path: '/tasks', query: { taskId: String(taskId), view: 'table' } })
}

const openAgentRun = (runId) => {
  router.push({ path: '/agent', query: { runId: String(runId) } })
}

const openDocumentTasks = () => {
  if (!docId.value) return
  router.push({
    path: '/tasks',
    query: {
      view: 'table',
      scope: 'all',
      sourceType: 'document',
      sourceId: String(docId.value),
    },
  })
}

const fetchRelatedAgentRuns = async (documentId) => {
  if (!documentId) {
    relatedAgentRuns.value = []
    return
  }
  try {
    const { data } = await api.listAgentRuns({ artifact_type: 'document', artifact_id: documentId, page: 1, page_size: 5 })
    relatedAgentRuns.value = data?.items || []
  } catch {
    relatedAgentRuns.value = []
  }
}

const openDocumentSourceSyncJob = () => {
  if (!documentSourceMeta.value?.syncJobId) return
  const query = {
    tab: 'connectors',
    connectorSyncJobId: String(documentSourceMeta.value.syncJobId),
  }
  if (docMeta.value?.connector_id) {
    query.connectorId = String(docMeta.value.connector_id)
  }
  router.push({ path: '/system', query })
}

const hydrateDocumentMeta = async (documentId) => {
  if (!documentId) return
  try {
    const { data } = await api.getDocument(documentId)
    docMeta.value = {
      ...docMeta.value,
      id: data.id,
      title: data.title,
      file_type: data.file_type,
      knowledge_base_id: data.knowledge_base_id,
      connector_id: data.connector_id || docMeta.value?.connector_id || null,
      connector_name: data.connector_name || docMeta.value?.connector_name || '',
      version_number: data.version_number,
      classification: data.classification,
      sensitivity_level: data.sensitivity_level,
      permission_scope: data.permission_scope,
      download_enabled: data.download_enabled !== false,
      watermark_required: Boolean(data.watermark_required),
      status: data.status,
      summary: data.summary,
      created_at: data.created_at,
      metadata_json: data.metadata_json || null,
    }
  } catch {
    // ignore detail hydration failure
  }
}

const downloadFilename = (response) => {
  const disposition = response.headers?.['content-disposition'] || ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  if (encoded) return decodeURIComponent(encoded)
  if (plain) return plain
  const extension = docMeta.value?.file_type ? `.${String(docMeta.value.file_type).replace(/^\./, '')}` : ''
  return `${docMeta.value?.title || 'document'}${extension}`
}

const downloadCurrentDocument = async () => {
  if (!docId.value || downloading.value) return
  downloading.value = true
  try {
    const response = await api.downloadDocument(docId.value)
    const url = URL.createObjectURL(response.data)
    const anchor = window.document.createElement('a')
    anchor.href = url
    anchor.download = downloadFilename(response)
    anchor.style.display = 'none'
    window.document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('受控下载已开始')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '文档下载失败')
  } finally {
    downloading.value = false
  }
}

const updateDownloadPolicy = async (field, value) => {
  if (!docId.value || downloadPolicySaving.value) return
  const previous = field === 'download_enabled'
    ? docMeta.value.download_enabled !== false
    : Boolean(docMeta.value.watermark_required)
  if (field === 'download_enabled' && value === false) {
    try {
      await ElMessageBox.confirm('关闭后，所有有查看权限的用户都无法下载该文档。', '确认禁止下载', {
        confirmButtonText: '禁止下载',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch {
      docMeta.value = { ...docMeta.value, [field]: previous }
      return
    }
  }
  downloadPolicySaving.value = true
  try {
    const { data } = await api.updateDocumentDownloadPolicy(docId.value, { [field]: value })
    docMeta.value = { ...docMeta.value, ...data }
    ElMessage.success(field === 'download_enabled' ? '下载策略已更新' : '水印策略已更新')
  } catch (error) {
    docMeta.value = { ...docMeta.value, [field]: previous }
    ElMessage.error(error.response?.data?.detail || '下载策略更新失败')
  } finally {
    downloadPolicySaving.value = false
  }
}

const openAgentDemo = () => {
  if (!docId.value) return
  router.push({
    path: '/agent',
    query: buildAgentDemoRouteQuery('document_risk', {
      documentId: docId.value,
      documentTitle: docMeta.value?.title || '',
    }),
  })
}

const selectDocument = async (item) => {
  docId.value = item.id
  docMeta.value = item
  compareResult.value = null
  createdTasks.value = []
  qaQuestion.value = ''
  qaResult.value = null
  negativeFeedbackVisible.value = false
  resetFeedbackForm()
  resetAnalysisTask()
  replaceDocumentQuery(item.id)
  await hydrateDocumentMeta(item.id)
  await fetchRelatedAgentRuns(item.id)
  await fetchParseJobs()
  await fetchVersions()
  await fetchQaRecords()
  await runAnalysis()
}

const runAnalysis = async () => {
  if (!docId.value) return

  // 检查是否已有进行中或已完成的分析任务，避免重复提交
  const existingJob = parseJobs.value.find((j) => j.job_type === 'document_analysis')
  if (existingJob) {
    if (existingJob.status === 'completed' && existingJob.task_id) {
      // 已完成：直接拉任务结果展示，不重新提交
      loading.value = true
      try {
        const { data } = await api.getDocumentTaskStatus(existingJob.task_id)
        if (data.result) {
          analysis.value = {
            summary: data.result.summary || '',
            risks: data.result.risks || [],
            todos: data.result.todos || [],
            clauses: data.result.clauses || [],
            structured_fields: normalizeStructuredFields(data.result.structured_fields),
            references: data.result.references || [],
          }
        }
      } catch {
        // 结果拉取失败时静默，不影响其他功能
      } finally {
        loading.value = false
      }
      return
    }
    if ((existingJob.status === 'pending' || existingJob.status === 'running') && existingJob.task_id) {
      // 进行中：接管轮询，不重新提交
      analysisTask.value = {
        taskId: existingJob.task_id,
        state: 'PENDING',
        message: existingJob.message || '文档分析任务进行中',
      }
      pollAnalysisTask(existingJob.task_id)
      return
    }
  }

  loading.value = true
  try {
    const { data } = await api.analyzeDocument(docId.value, 500, true)
    if (data.async_mode && data.task_id) {
      analysis.value = emptyAnalysis()
      analysisTask.value = {
        taskId: data.task_id,
        state: data.state || 'PENDING',
        message: '文档分析任务已提交',
      }
      pollAnalysisTask(data.task_id)
      return
    }
    analysis.value = {
      summary: data.summary || '',
      risks: data.risks || [],
      todos: data.todos || [],
      clauses: data.clauses || [],
      structured_fields: normalizeStructuredFields(data.structured_fields),
      references: data.references || [],
    }
  } catch (e) {
    analysis.value = emptyAnalysis()
    ElMessage.error(e.response?.data?.detail || '文档分析失败')
  } finally {
    loading.value = false
  }
}

const pollAnalysisTask = async (taskId) => {
  clearAnalysisPolling()
  try {
    const { data } = await api.getDocumentTaskStatus(taskId)
    analysisTask.value = {
      taskId,
      state: data.state,
      message: data.info?.step || data.error || '',
    }

    if (data.state === 'SUCCESS' && data.result) {
      analysis.value = {
        summary: data.result.summary || '',
        risks: data.result.risks || [],
        todos: data.result.todos || [],
        clauses: data.result.clauses || [],
        structured_fields: normalizeStructuredFields(data.result.structured_fields),
        references: data.result.references || [],
      }
      analysisTask.value.message = '文档分析已完成'
      await fetchDocuments()
      await fetchParseJobs()
      return
    }

    if (data.state === 'FAILURE') {
      analysis.value = emptyAnalysis()
      await fetchParseJobs()
      ElMessage.error(data.error || '文档分析失败')
      return
    }

    analysisPollingTimer = setTimeout(() => pollAnalysisTask(taskId), 1500)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '获取分析任务状态失败')
  }
}

const runCompare = async () => {
  if (compareSelection.value.length < 2) {
    ElMessage.warning('至少选择两份文档')
    return
  }
  compareLoading.value = true
  try {
    const { data } = await api.compareDocuments(compareSelection.value)
    compareResult.value = data
    conflictCases.value = []
  } catch (e) {
    compareResult.value = null
    ElMessage.error(e.response?.data?.detail || '文档对比失败')
  } finally {
    compareLoading.value = false
  }
}

const createConflictSuggestions = async () => {
  const conflicts = (compareResult.value?.comparison?.conflict_analysis?.conflicts || []).filter((item) => item.evidence_complete)
  if (!conflicts.length) return
  conflictSuggestionLoading.value = true
  try {
    const { data } = await api.createConflictSuggestions({ document_ids: compareSelection.value, conflicts })
    conflictCases.value = data.items || []
    ElMessage.success(`已生成 ${conflictCases.value.length} 项待确认风险任务建议`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '风险任务建议生成失败')
  } finally {
    conflictSuggestionLoading.value = false
  }
}

const confirmConflictTask = async (item) => {
  try {
    await ElMessageBox.confirm('将创建一条可追溯的内部风险任务，任务说明会包含双侧原文证据和定位信息。', '确认创建风险任务', { type: 'warning', confirmButtonText: '确认创建', cancelButtonText: '取消' })
    const { data } = await api.confirmConflictTask(item.id)
    const index = conflictCases.value.findIndex((caseItem) => caseItem.id === item.id)
    if (index >= 0) conflictCases.value[index] = data.case
    ElMessage.success(`风险任务 #${data.task.id} 已创建`)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e.response?.data?.detail || '风险任务创建失败')
  }
}

const uploadAndAnalyze = async () => {
  if (!selectedFiles.value.length) return
  uploading.value = true
  try {
      const payload = {
        knowledge_base_name: uploadForm.value.knowledge_base_name || undefined,
        classification: uploadForm.value.classification || undefined,
        tags: uploadForm.value.tags || undefined,
        sensitivity_level: uploadForm.value.sensitivity_level || undefined,
        permission_scope: uploadForm.value.permission_scope || undefined,
        permission_users: uploadForm.value.permission_users || undefined,
        permission_roles: uploadForm.value.permission_roles || undefined,
    }
    const request = selectedFiles.value.length > 1
      ? api.batchUploadDocuments(selectedFiles.value, true, payload)
      : api.uploadDocument(selectedFiles.value[0], true, payload)
    const { data } = await request
    const firstDocument = data.documents?.[0] || data
    docId.value = firstDocument.id
    docMeta.value = firstDocument
    analysis.value = emptyAnalysis()
    versions.value = []
    createdTasks.value = []
    compareResult.value = null
    qaQuestion.value = ''
    qaResult.value = null
    negativeFeedbackVisible.value = false
    resetFeedbackForm()
    selectedFiles.value = []
    file.value = null
    ElMessage.success(data.count ? `已上传 ${data.count} 份文档` : '文档上传成功')
    await fetchDocuments()
    await fetchKnowledgeBases()
    await fetchParseJobs()
    await fetchVersions()
    await fetchQaRecords()
    await runAnalysis()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

const retryParse = async () => {
  if (!docId.value) return
  try {
    await api.retryDocumentParse(docId.value)
    ElMessage.success('已提交解析重试任务')
    await fetchParseJobs()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重试解析失败')
  }
}

const createTasks = async () => {
  if (!docId.value) return
  creatingTasks.value = true
  try {
    const { data } = await api.createTasksFromDocument(docId.value)
    createdTasks.value = data.tasks || []
    ElMessage.success(`已创建 ${data.created_tasks || 0} 条任务`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建任务失败')
  } finally {
    creatingTasks.value = false
  }
}

const loadDocumentFromRoute = async (rawDocumentId) => {
  const nextId = Number(rawDocumentId)
  if (!Number.isFinite(nextId) || nextId <= 0) return

  const existing = documents.value.find((item) => item.id === nextId)
  if (existing) {
    if (docId.value !== existing.id) {
      await selectDocument(existing)
    }
    return
  }

  try {
    const { data } = await api.getDocument(nextId)
    let connectorId = data.connector_id || null
    let connectorName = data.connector_name || ''
    if ((!connectorId || !connectorName) && data.metadata_json) {
      try {
        const parsed = JSON.parse(data.metadata_json)
        connectorId = connectorId || parsed?.connector_id || null
        connectorName = connectorName || parsed?.connector_name || ''
      } catch {
        // ignore invalid metadata
      }
    }
    const normalized = {
      id: data.id,
      title: data.title,
      file_type: data.file_type,
      knowledge_base_id: data.knowledge_base_id,
      connector_id: connectorId,
      connector_name: connectorName,
      version_number: data.version_number,
      classification: data.classification,
      sensitivity_level: data.sensitivity_level,
      permission_scope: data.permission_scope,
      status: data.status,
      summary: data.summary,
      created_at: data.created_at,
      metadata_json: data.metadata_json || null,
    }
    documents.value = [normalized, ...documents.value.filter((item) => item.id !== normalized.id)]
    await selectDocument(normalized)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '文档加载失败')
  }
}

onMounted(async () => {
  analysis.value = emptyAnalysis()
  await fetchKnowledgeBases()
  await fetchConnectors()
  await fetchDocuments()
  await loadDocumentFromRoute(route.query.documentId)
})

watch(
  () => route.query.documentId,
  async (value, oldValue) => {
    if (value === oldValue) return
    await loadDocumentFromRoute(value)
  }
)

watch(
  () => route.query.connectorId,
  async (value, oldValue) => {
    if (value === oldValue) return
    filters.value.connector_id = normalizeConnectorId(value)
    documentPage.value = 1
    await fetchDocuments()
  }
)

onUnmounted(() => {
  clearAnalysisPolling()
})
</script>

<style scoped>
.documents-page {
  display: grid;
  gap: var(--space-6);
  max-width: 1600px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  gap: var(--space-6);
  align-items: stretch;
  padding: var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--gradient-hero);
  box-shadow: var(--shadow-xs);
}

.page-heading-copy {
  max-width: 680px;
}
.page-header h3 {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text);
  font-size: var(--text-3xl);
  font-weight: 800;
  letter-spacing: 0;
}
.page-header p {
  max-width: 720px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: var(--text-sm);
}

.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.overview-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.metric-tile {
  padding: var(--space-5) var(--space-5);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.metric-tile:hover {
  box-shadow: var(--shadow-card-hover);
  border-color: var(--color-border-hover);
  transform: translateY(-2px);
}
.metric-tile span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.metric-tile strong {
  font-size: var(--text-3xl);
  line-height: var(--text-3xl-lh);
  color: var(--color-text);
  font-weight: 800;
}

.upload-console {
  width: min(420px, 100%);
  display: grid;
  gap: var(--space-2);
}
.upload-dropzone {
  width: 100%;
}
:deep(.upload-dropzone .el-upload) {
  width: 100%;
}
:deep(.upload-dropzone .el-upload-dragger) {
  width: 100%;
  height: 124px;
  border-radius: var(--radius-md);
  border-color: var(--color-border-hover);
  background: var(--color-primary-light);
  padding: 0;
  transition: all var(--transition-fast);
}
:deep(.upload-dropzone .el-upload-dragger:hover) {
  border-color: var(--color-primary);
  background: #EAF8FF;
  box-shadow: 0 0 0 4px var(--color-primary-subtle);
}
.upload-dropzone-inner {
  height: 100%;
  display: grid;
  place-content: center;
  gap: var(--space-1);
  text-align: center;
}
.upload-dropzone-inner strong {
  color: var(--color-primary);
  font-size: var(--text-base);
}
.upload-dropzone-inner span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.upload-console-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.layout-grid {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: var(--space-6);
}

.sidebar-card,
.toolbar-card,
.panel-card,
.task-card,
.compare-result-card,
.mini-card {
  border-radius: var(--radius-lg);
}

.sidebar-card {
  position: sticky;
  top: 0;
  align-self: start;
}

.card-header-inline {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
}

.current-doc,
.compare-box,
.governance-box,
.filter-box,
.retrieval-box {
  margin-bottom: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: #ffffff;
  box-shadow: var(--shadow-xs);
}
.current-doc {
  background: var(--color-primary-light);
}
.current-doc-head {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: var(--space-2);
}
.compare-box {
  background: var(--color-warning-light);
  display: grid;
  gap: var(--space-2);
}
.governance-box {
  background: #EAF8FF;
  display: grid;
  gap: var(--space-2);
}
.filter-box {
  background: var(--color-bg-alt);
  display: grid;
  gap: var(--space-2);
}
.retrieval-box {
  display: grid;
  gap: var(--space-2);
}
.retrieval-box p {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}
.retrieval-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}
.retrieval-form-grid label {
  display: grid;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.compare-title {
  font-size: var(--text-sm);
  font-weight: 700;
  color: var(--color-text);
}

.list-section-head,
.content-section-head {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
.list-section-head {
  margin: var(--space-2) 0 var(--space-3);
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.content-section-head {
  padding: 0 4px;
}
.content-section-head h4 {
  margin: 0;
  font-size: var(--text-xl);
  color: var(--color-text);
}
.content-section-head span {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.compare-button {
  width: 100%;
}

.doc-title {
  font-weight: 700;
  color: var(--color-text);
}
.doc-tags {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-2);
  flex-wrap: wrap;
}
.doc-source-meta {
  margin-top: var(--space-2);
  display: grid;
  gap: var(--space-1);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.download-governance {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px dashed rgba(29, 78, 216, 0.22);
  display: grid;
  gap: var(--space-3);
}
.download-governance-copy {
  display: grid;
  gap: 2px;
}
.download-governance-copy strong {
  color: var(--color-text);
  font-size: var(--text-sm);
}
.download-governance-copy span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.5;
}
.download-policy-controls {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}
.download-policy-controls label {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

.doc-list {
  display: grid;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.doc-item {
  width: 100%;
  text-align: left;
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  cursor: pointer;
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.doc-item:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}
.doc-item:active {
  transform: translateY(0);
}
.doc-item.active {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  box-shadow: 0 0 0 2px var(--color-primary-subtle);
}
.doc-item strong {
  color: var(--color-text);
}
.doc-item span,
.toolbar-meta {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.doc-item-foot {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  flex-wrap: wrap;
}

.main-column {
  display: grid;
  gap: var(--space-6);
}

.workspace-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
}

.workspace-tile {
  padding: var(--space-5) var(--space-5);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.workspace-tile:hover {
  box-shadow: var(--shadow-card-hover);
  border-color: var(--color-border-hover);
  transform: translateY(-2px);
}
.workspace-tile span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.workspace-tile strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}
.workspace-tile p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: var(--text-sm);
}

.insight-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(320px, 0.9fr);
  gap: var(--space-4);
  padding: var(--space-5);
  border-radius: var(--radius-xl);
  border: 1px solid var(--color-border-light);
  background: var(--gradient-hero);
  box-shadow: var(--shadow-xs);
}
.insight-hero-copy {
  display: grid;
  gap: var(--space-2);
}
.summary-label,
.insight-metric span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.insight-hero-copy strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.insight-hero-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.insight-hero-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}
.insight-metric {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  display: grid;
  gap: var(--space-1);
}
.insight-metric strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}

.async-status {
  margin-top: var(--space-3);
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}

.analysis-grid,
.compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-6);
}

.summary-card-surface,
.analysis-card {
  overflow: hidden;
}
.analysis-card :deep(.el-card__body) {
  display: grid;
  gap: var(--space-3);
}
.panel-summary,
.panel-fields,
.panel-reference {
  grid-column: 1 / -1;
}

.rich-text {
  white-space: pre-wrap;
  line-height: 1.8;
  color: var(--color-text);
}

.stack-list,
.reference-list {
  display: grid;
  gap: var(--space-3);
}

.structured-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}
.structured-block {
  display: grid;
  gap: var(--space-3);
}

.qa-compose,
.qa-result,
.qa-feedback-form,
.qa-citations,
.qa-history-citations {
  display: grid;
  gap: var(--space-3);
}
.qa-compose-actions,
.qa-result-top,
.qa-feedback-actions {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
.qa-feedback-form {
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--color-warning-light);
}

.stack-item,
.reference-item {
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  border: 1px solid var(--color-border-light);
  transition: all var(--transition-fast);
}
.stack-item:hover,
.reference-item:hover {
  border-color: var(--color-primary-subtle);
}

.risk-item {
  border-left: 4px solid var(--color-accent);
}
.todo-item {
  border-left: 4px solid var(--color-primary);
}
.clause-item {
  border-left: 4px solid var(--color-success);
}

.stack-top,
.stack-foot,
.reference-label {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.stack-item p,
.compare-overview p {
  margin: var(--space-2) 0;
  color: var(--color-text);
  line-height: 1.7;
}
.stack-foot {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}

.reference-item blockquote {
  margin: var(--space-3) 0 0;
  padding: 0 0 0 var(--space-3);
  border-left: 3px solid var(--color-border);
  color: var(--color-text);
  line-height: 1.7;
}
.citation-item {
  background: var(--color-primary-light);
}

.compare-overview {
  margin-bottom: var(--space-5);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
}

.compare-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(320px, 0.9fr);
  gap: var(--space-4);
  padding: var(--space-5);
  border-radius: var(--radius-xl);
  border: 1px solid var(--color-border-light);
  background: var(--gradient-hero);
  margin-bottom: var(--space-5);
}
.compare-hero-copy {
  display: grid;
  gap: var(--space-2);
}
.compare-hero-copy strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.compare-hero-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.compare-hero-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}
.compare-metric {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  display: grid;
  gap: var(--space-1);
}
.compare-metric span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.compare-metric strong {
  color: var(--color-text);
  font-size: var(--text-xl);
}
.conflict-metric { border-color: var(--el-color-danger-light-7); }
.conflict-card { grid-column: span 2; }
.conflict-item { border-left: 3px solid var(--el-color-danger); }
.conflict-source { display: grid; grid-template-columns: minmax(120px, .8fr) minmax(110px, .55fr); gap: var(--space-1) var(--space-2); padding: var(--space-2) 0; border-top: 1px solid var(--color-border-light); font-size: var(--text-sm); }
.conflict-source b { color: var(--color-text); }.conflict-source span { color: var(--el-color-danger); font-weight: 700; }.conflict-source small { grid-column: 1 / -1; color: var(--color-text-secondary); line-height: 1.55; }
.conflict-actions { justify-content: flex-start; margin-top: var(--space-4); }
.conflict-case-list { display: grid; gap: var(--space-2); margin-top: var(--space-4); }
.conflict-case-row { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; padding: var(--space-3); background: var(--color-bg-alt); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); }
.conflict-case-row > div { display: grid; gap: 2px; flex: 1; min-width: 170px; }.conflict-case-row span { color: var(--color-text-secondary); font-size: var(--text-sm); }

.compare-overview h4 {
  margin: 0 0 var(--space-2);
}

.simple-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: var(--space-2);
  color: var(--color-text);
}

@media (max-width: 1100px) {
  .overview-metrics,
  .layout-grid,
  .workspace-strip,
  .insight-hero,
  .compare-hero,
  .analysis-grid,
  .compare-grid,
  .structured-grid {
    grid-template-columns: 1fr;
  }
  .conflict-card { grid-column: auto; }
  .page-header {
    display: grid;
  }
  .sidebar-card {
    position: static;
  }
}
</style>
