<template>
        <div class="tab-panel">
          <!-- 资料库范围：法规与案例 / 案件材料（下划线页签，样式走令牌） -->
          <div class="library-mode-switch" role="tablist" aria-label="资料库范围">
            <button type="button" :class="{ active: libraryMode === 'sources' }" @click="libraryMode = 'sources'">法规与案例</button>
            <button type="button" :class="{ active: libraryMode === 'materials' }" @click="libraryMode = 'materials'">案件材料</button>
          </div>

          <!-- 检索条：方向 B 卡片头（标题 + 条数）/ 卡片体（搜索 + 筛选 + 导出） -->
          <section class="app-card">
            <div class="app-card-head">
              <h2>资料检索</h2>
              <span class="app-card-head-meta">{{ libraryMode === 'sources' ? `${displaySources.length} 条资料` : `${filteredMaterials.length} 份材料` }}</span>
            </div>
            <div class="app-card-body research-toolbar">
              <div class="research-search">
                <label for="legal-source-search">搜索资料</label>
                <div class="research-search-row">
                  <el-input id="legal-source-search" v-model="retrievalQuestion" :placeholder="libraryMode === 'sources' ? '搜索法规、案例、合同模板或文书模板' : '搜索案件材料标题'" clearable @keyup.enter="runSourceSearch" />
                  <button type="button" class="workspace-primary is-compact" :disabled="retrievalLoading || materialLoading" @click="runSourceSearch">搜索</button>
                </div>
              </div>
              <div class="research-actions">
                <div v-if="libraryMode === 'sources'" class="research-filters" aria-label="资料筛选">
                  <el-select v-model="sourceTypeFilter" clearable size="small" placeholder="全部类型">
                    <el-option label="法规" value="statute" /><el-option label="案例" value="case" />
                    <el-option label="合同模板" value="template" /><el-option label="司法解释" value="judicial_interpretation" />
                  </el-select>
                  <el-select v-model="sourceStatusFilter" clearable size="small" placeholder="全部状态">
                    <el-option label="当前有效" value="active" /><el-option label="待更新" value="pending_update" /><el-option label="已失效" value="inactive" />
                  </el-select>
                  <el-checkbox v-model="favoritesOnly" label="只看收藏" size="small" />
                </div>
                <div v-else class="research-filters" aria-label="案件材料筛选">
                  <el-select v-model="materialKindFilter" clearable size="small" placeholder="全部材料">
                    <el-option label="合同" value="contract" /><el-option label="证据" value="evidence" />
                    <el-option label="法规" value="statute" /><el-option label="案例" value="case" />
                  </el-select>
                </div>
                <el-button v-if="libraryMode === 'sources'" text size="small" @click="exportSources"><el-icon><Download /></el-icon>导出结果</el-button>
              </div>
            </div>
          </section>

          <!-- 两栏骨架：左资料目录（rail 形态）/ 右阅读器 -->
          <div v-if="libraryMode === 'sources'" class="research-layout">
            <aside class="app-card source-catalog" aria-label="资料目录">
              <div class="app-card-head">
                <h2>资料目录</h2>
                <span class="app-card-head-meta">{{ displaySources.length }} 条</span>
              </div>
              <div class="app-card-body catalog-body">
                <QueryStateView :status="sourcesStatus" :error="sourcesError" empty-text="没有符合条件的资料。" min-height="160px" @retry="reloadSources">
                  <div class="app-rail source-catalog-rows">
                    <button
                      v-for="source in displaySources"
                      :key="source.id"
                      type="button"
                      class="app-rail-item source-catalog-row"
                      :class="{ active: selectedSource?.id === source.id }"
                      @click="openSource(source)"
                    >
                      <span class="app-rail-item-title source-catalog-title">{{ source.title }}</span>
                      <span class="app-rail-item-meta source-catalog-meta"><span>{{ sourceTypeLabel(source.source_type) }}</span><span>{{ sourceStatusLabel(source.status) }}</span><span v-if="source.is_favorite" class="source-favorite-mark" title="已收藏">★</span><span v-if="source.is_linked" title="已关联当前案件">案件</span></span>
                    </button>
                  </div>
                </QueryStateView>
              </div>
            </aside>

            <article v-if="selectedSource" class="app-card source-reader" aria-label="资料阅读器">
              <header class="app-card-head source-reader-header">
                <div class="source-reader-heading">
                  <p class="source-reader-kicker">{{ sourceTypeLabel(selectedSource.source_type) }}</p>
                  <h2>{{ selectedSource.title }}</h2>
                  <p v-if="selectedSource.citation" class="source-reader-citation">{{ selectedSource.citation }}</p>
                </div>
                <div class="source-reader-status">
                  <span class="app-tag" :class="sourceStatusTagClass(selectedSource.status)">{{ sourceStatusLabel(selectedSource.status) }}</span>
                  <span v-if="selectedSource.version" class="source-reader-version">版本 {{ selectedSource.version }}</span>
                  <el-button text size="small" :type="selectedSource.is_favorite ? 'warning' : 'default'" @click="toggleFavorite(selectedSource)"><el-icon><Star /></el-icon>{{ selectedSource.is_favorite ? '已收藏' : '收藏' }}</el-button>
                  <el-button v-if="caseId" text size="small" :type="selectedSource.is_linked ? 'success' : 'default'" @click="toggleCaseLink(selectedSource)"><el-icon><Link /></el-icon>{{ selectedSource.is_linked ? '已关联' : '关联案件' }}</el-button>
                  <el-button text size="small" @click="exportSource(selectedSource)"><el-icon><Download /></el-icon>导出</el-button>
                </div>
              </header>

              <div class="app-card-body source-reader-body">
                <dl class="source-facts">
                  <div v-if="selectedSource.promulgator"><dt>发布机关</dt><dd>{{ selectedSource.promulgator }}</dd></div>
                  <div v-if="selectedSource.document_number"><dt>发文字号</dt><dd>{{ selectedSource.document_number }}</dd></div>
                  <div v-if="selectedSource.jurisdiction"><dt>适用范围</dt><dd>{{ selectedSource.jurisdiction }}</dd></div>
                  <div v-if="selectedSource.effective_date"><dt>生效日期</dt><dd>{{ selectedSource.effective_date }}</dd></div>
                  <div v-if="selectedSource.expiration_date"><dt>失效日期</dt><dd>{{ selectedSource.expiration_date }}</dd></div>
                  <div v-if="selectedSource.applicability_scope"><dt>适用事项</dt><dd>{{ selectedSource.applicability_scope }}</dd></div>
                </dl>

                <div class="source-reader-content">
                  <div v-if="selectedSource.content" class="source-summary"><span class="source-body-label">资料摘要</span><p>{{ selectedSource.content }}</p></div>
                  <div v-loading="readerLoading" class="source-article-list">
                    <div v-if="sourceArticles.length" class="source-body-label">正文与条文</div>
                    <article v-for="article in sourceArticles" :key="article.id" class="source-article">
                      <div class="source-article-heading"><strong>{{ article.article_number }}</strong><span v-if="article.title">{{ article.title }}</span></div>
                      <p><template v-for="(part, index) in articleParts(article.content)" :key="index"><mark v-if="part.hit">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></p>
                    </article>
                    <p v-if="!readerLoading && !sourceArticles.length" class="source-full-text">{{ selectedSource.full_text || selectedSource.content || '该资料暂无可阅读正文。' }}</p>
                  </div>
                </div>
              </div>
            </article>
            <div v-else class="app-card source-reader-empty">从左侧选择一份资料开始阅读。</div>
          </div>

          <!-- 案件材料：同一信息架构下的单栏列表卡 -->
          <section v-else class="app-card material-library">
            <div class="app-card-head">
              <h2>案件材料</h2>
              <span class="app-card-head-meta">{{ filteredMaterials.length }} 份</span>
            </div>
            <div class="app-card-body material-body">
              <div v-if="materialLoading" class="material-note">正在加载案件材料…</div>
              <button v-for="item in filteredMaterials" :key="item.id" type="button" class="material-library-row" @click="openMaterial(item)">
                <span class="material-kind">{{ documentKindLabel(item) }}</span>
                <span class="material-main"><strong>{{ item.title }}</strong><small>{{ item.file_type || '文档' }} · v{{ item.version_number || 1 }} · {{ item.status || '处理中' }}</small></span>
                <span class="material-date">{{ formatDate(item.created_at) }}</span>
              </button>
              <div v-if="!materialLoading && !filteredMaterials.length" class="material-note">当前没有符合条件的案件材料。</div>
            </div>
          </section>

          <!-- 资料维护：导入 / 检索测试 / 资料库（折叠区，交互不变） -->
          <details class="source-maintenance">
            <summary>资料维护 <span>导入、编辑与状态管理</span></summary>
            <div class="maintenance-stack">
              <section class="app-card">
                <div class="app-card-head">
                  <h2>资料导入</h2>
                  <el-upload class="import-upload" :before-upload="handleSourceImport" :show-file-list="false" accept=".csv,.xlsx,.xls">
                    <button type="button" class="workspace-primary is-compact" :disabled="importLoading">
                      <el-icon><Upload /></el-icon>
                      导入资料表
                    </button>
                  </el-upload>
                </div>
                <div class="app-card-body">
                  <el-alert type="info" :closable="false" show-icon>
                    <p><strong>CSV 格式要求：</strong></p>
                    <ul class="import-format-list">
                      <li>必需列：title（标题）, source_type（类型）, content（内容）</li>
                      <li>可选列：citation（引用条款）, jurisdiction（适用地域）, version（版本）, effective_date（生效日期 YYYY-MM-DD）, status（状态）</li>
                      <li>source_type 值：statute（法律法规）, case（案例摘要）, template（合同模板）</li>
                      <li>status 值：active（当前有效）, inactive（已失效）, pending_update（待更新）</li>
                    </ul>
                  </el-alert>
                  <div v-if="importResult" class="import-result">
                    <span class="app-tag" :class="importResult.skipped > 0 ? 'is-pending' : 'is-ok'">
                      导入成功 {{ importResult.imported }} 条，跳过 {{ importResult.skipped }} 条
                    </span>
                    <div v-if="importResult.errors?.length" class="import-error-block">
                      <strong>错误详情：</strong>
                      <ul class="import-errors">
                        <li v-for="(err, i) in importResult.errors" :key="i">{{ err }}</li>
                      </ul>
                    </div>
                  </div>
                </div>
              </section>

              <section class="app-card">
                <div class="app-card-head"><h2>资料搜索</h2></div>
                <div class="app-card-body">
                  <el-form @submit.prevent="submitRetrievalTest">
                    <el-form-item label="搜索资料">
                      <el-input v-model="retrievalQuestion" placeholder="搜索法规、案例、合同模板或文书模板..." @keyup.enter="submitRetrievalTest" />
                    </el-form-item>
                    <button type="button" class="workspace-primary" :disabled="retrievalLoading" @click="submitRetrievalTest">搜索</button>
                  </el-form>

                  <div v-if="retrievalResult" class="retrieval-result">
                    <p class="summary-text">共 {{ retrievalResult.total_sources }} 条资料，找到 {{ retrievalResult.results.filter(r => r.total_score > 0).length }} 条相关结果</p>
                    <el-table :data="retrievalResult.results" stripe size="small" max-height="400">
                      <el-table-column prop="title" label="法源名称" show-overflow-tooltip />
                      <el-table-column prop="total_score" label="相关性" width="90" sortable>
                        <template #default="{ row }">
                          <span class="app-tag" :class="row.total_score > 0 ? 'is-ok' : ''">{{ row.total_score }}</span>
                        </template>
                      </el-table-column>
                      <el-table-column label="匹配信息" min-width="220">
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
                          <span class="app-tag" :class="sourceStatusTagClass(row.status)">{{ sourceStatusLabel(row.status) }}</span>
                        </template>
                      </el-table-column>
                      <el-table-column label="命中关键词" show-overflow-tooltip>
                        <template #default="{ row }">{{ row.matched_keywords.join('、') || '无' }}</template>
                      </el-table-column>
                    </el-table>
                  </div>
                </div>
              </section>

              <section class="app-card">
                <div class="app-card-head">
                  <h2>资料库</h2>
                  <span class="app-tag is-info">失效 / 待更新 / 当前有效</span>
                  <button type="button" class="workspace-primary is-compact maintenance-cta" @click="openSourceDialog()">新建法源</button>
                </div>
                <div class="app-card-body">
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
                </div>
              </section>

              <el-dialog v-model="sourceDialogVisible" :title="editingSource ? '编辑资料' : '新建资料'" width="680px">
                <el-form :model="sourceForm" label-width="110px" size="small">
                  <div class="source-form-row">
                    <div class="source-form-cell">
                      <el-form-item label="标题" required>
                        <el-input v-model="sourceForm.title" placeholder="如《劳动合同法》" />
                      </el-form-item>
                    </div>
                    <div class="source-form-cell">
                      <el-form-item label="类型" required>
                        <el-select v-model="sourceForm.source_type" style="width:100%">
                          <el-option label="法律法规" value="statute" />
                          <el-option label="司法解释" value="judicial_interpretation" />
                          <el-option label="案例摘要" value="case" />
                          <el-option label="合同模板" value="template" />
                        </el-select>
                      </el-form-item>
                    </div>
                  </div>
                  <div class="source-form-row">
                    <div class="source-form-cell">
                      <el-form-item label="发文字号">
                        <el-input v-model="sourceForm.document_number" placeholder="如：主席令第65号" />
                      </el-form-item>
                    </div>
                    <div class="source-form-cell">
                      <el-form-item label="发布机关">
                        <el-input v-model="sourceForm.promulgator" placeholder="如：全国人大常委会" />
                      </el-form-item>
                    </div>
                  </div>
                  <div class="source-form-row">
                    <div class="source-form-cell">
                      <el-form-item label="引用条款">
                        <el-input v-model="sourceForm.citation" placeholder="如：劳动合同法第40条" />
                      </el-form-item>
                    </div>
                    <div class="source-form-cell">
                      <el-form-item label="管辖地域">
                        <el-input v-model="sourceForm.jurisdiction" placeholder="中国大陆" />
                      </el-form-item>
                    </div>
                  </div>
                  <div class="source-form-row is-three">
                    <div class="source-form-cell">
                      <el-form-item label="版本">
                        <el-input v-model="sourceForm.version" placeholder="v1" />
                      </el-form-item>
                    </div>
                    <div class="source-form-cell">
                      <el-form-item label="状态">
                        <el-select v-model="sourceForm.status" style="width:100%">
                          <el-option label="当前有效" value="active" />
                          <el-option label="已失效" value="inactive" />
                          <el-option label="待更新" value="pending_update" />
                        </el-select>
                      </el-form-item>
                    </div>
                    <div class="source-form-cell">
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
                    </div>
                  </div>
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
          </details>
        </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import 'element-plus/es/components/message-box/style/css'
import { ElUpload } from 'element-plus/es/components/upload/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElCheckbox } from 'element-plus/es/components/checkbox/index'
import 'element-plus/es/components/upload/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/checkbox/style/css'
import { Upload } from '@element-plus/icons-vue'
import { Download, Link, Star } from '@element-plus/icons-vue'
import legalWorkspace from '../../api/legalWorkspace'
import api from '../../api'
import { useLegalSources } from '../../composables/useLegalSources'
import { sourceStatusLabel, sourceTypeLabel } from '../../composables/useLegalWorkspacePresentation'
import QueryStateView from '../QueryStateView.vue'

const {
  legalSources,
  sourcesLoading,
  sourcesError,
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

const props = defineProps({ caseId: { type: [Number, String], default: null } })
const router = useRouter()
const caseId = computed(() => Number(props.caseId) > 0 ? Number(props.caseId) : null)
const libraryMode = ref('sources')
const favoritesOnly = ref(false)
const materialKindFilter = ref('')
const materials = ref([])
const materialLoading = ref(false)

const sourceTypeFilter = ref('')
const sourceStatusFilter = ref('')
const selectedSource = ref(null)
const sourceArticles = ref([])
const readerLoading = ref(false)
const displaySources = computed(() => {
  const filterRows = (rows) => rows.filter((source) => {
    if (sourceTypeFilter.value && source.source_type !== sourceTypeFilter.value) return false
    if (sourceStatusFilter.value && source.status !== sourceStatusFilter.value) return false
    return true
  })
  if (retrievalResult.value?.results?.length) {
    const sourceMap = new Map(legalSources.value.map((source) => [source.id, source]))
    const ranked = retrievalResult.value.results.map((result) => ({ ...sourceMap.get(result.source_id), ...result })).filter((source) => source.id)
    const rows = favoritesOnly.value ? ranked.filter((source) => source.is_favorite) : ranked
    return filterRows(rows)
  }
  const rows = favoritesOnly.value ? legalSources.value.filter((source) => source.is_favorite) : legalSources.value
  return filterRows(rows)
})
// 法源库三态（ux-audit P1-3/M-13）：加载失败不再伪装成"没有资料"，可重试
const sourcesStatus = computed(() => {
  if (sourcesLoading.value && !legalSources.value.length) return 'loading'
  if (sourcesError.value) return 'error'
  if (!displaySources.value.length) return 'empty'
  return 'success'
})
const reloadSources = () => loadLegalSources(caseId.value ? { params: { case_id: caseId.value } } : {})

// 状态 → 方向 B 共享标签（.app-tag 的 is-* 变体）；语义与原先 Element 的 success / info / warning 一一对应
const sourceStatusTagClass = (status) => ({ active: 'is-ok', inactive: '', pending_update: 'is-pending' }[status] ?? '')

const documentKind = (item) => item.document_kind || item.classification || ''
const materialKindMap = { contract: ['contract', 'contract_template'], evidence: ['evidence', 'case_material'], statute: ['statute', 'regulation', 'judicial_interpretation'], case: ['case', 'case_summary'] }
const documentKindLabel = (item) => {
  const kind = documentKind(item)
  if (materialKindMap.contract.includes(kind)) return '合同'
  if (materialKindMap.evidence.includes(kind)) return '证据'
  if (materialKindMap.statute.includes(kind)) return '法规'
  if (materialKindMap.case.includes(kind)) return '案例'
  return kind || '材料'
}
const filteredMaterials = computed(() => {
  const query = retrievalQuestion.value.trim().toLowerCase()
  return materials.value.filter((item) => {
    if (materialKindFilter.value && !materialKindMap[materialKindFilter.value]?.includes(documentKind(item))) return false
    return !query || String(item.title || '').toLowerCase().includes(query)
  })
})
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'

const openSource = async (source) => {
  selectedSource.value = source
  sourceArticles.value = []
  readerLoading.value = true
  try {
    const { data } = await legalWorkspace.getSourceArticles(source.id)
    sourceArticles.value = data || []
  } catch {
    sourceArticles.value = []
  } finally {
    readerLoading.value = false
  }
}

const loadMaterials = async () => {
  materialLoading.value = true
  try {
    const { data } = await api.listDocuments({ page: 1, page_size: 100, ...(caseId.value ? { case_id: caseId.value } : {}) })
    materials.value = data?.items || []
  } catch {
    materials.value = []
  } finally {
    materialLoading.value = false
  }
}

const openMaterial = (item) => {
  router.push({ path: '/documents', query: { documentId: String(item.id), ...(caseId.value ? { case_id: String(caseId.value) } : {}) } })
}

const toggleFavorite = async (source) => {
  const next = !source.is_favorite
  try {
    await (next ? legalWorkspace.favoriteSource(source.id) : legalWorkspace.unfavoriteSource(source.id))
    source.is_favorite = next
    const canonical = legalSources.value.find((item) => item.id === source.id)
    if (canonical) canonical.is_favorite = next
    if (favoritesOnly.value && !next) {
      const index = legalSources.value.findIndex((item) => item.id === source.id)
      if (index >= 0) legalSources.value.splice(index, 1)
    }
    ElMessage.success(next ? '已收藏资料' : '已取消收藏')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '收藏状态更新失败')
  }
}

const toggleCaseLink = async (source) => {
  if (!caseId.value) return
  const next = !source.is_linked
  try {
    await (next ? legalWorkspace.linkSourceToCase(source.id, caseId.value) : legalWorkspace.unlinkSourceFromCase(source.id, caseId.value))
    source.is_linked = next
    const canonical = legalSources.value.find((item) => item.id === source.id)
    if (canonical) canonical.is_linked = next
    ElMessage.success(next ? '已关联当前案件' : '已取消案件关联')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '案件关联更新失败')
  }
}

const articleParts = (content) => {
  const text = String(content || '')
  const query = retrievalQuestion.value.trim()
  if (!query) return [{ text, hit: false }]
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const chunks = text.split(new RegExp(`(${escaped})`, 'ig'))
  return chunks.filter(Boolean).map((part) => ({ text: part, hit: part.toLowerCase() === query.toLowerCase() }))
}

const downloadText = (filename, content, type = 'text/plain;charset=utf-8') => {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
const exportSource = (source) => {
  const lines = [`# ${source.title}`, '', `类型：${sourceTypeLabel(source.source_type)}`, `状态：${sourceStatusLabel(source.status)}`, `版本：${source.version || '未记录'}`, source.citation ? `引用：${source.citation}` : '', '', '## 摘要', source.content || '', '', '## 正文', source.full_text || sourceArticles.value.map((article) => `${article.article_number} ${article.content}`).join('\n\n') || '暂无正文']
  downloadText(`${source.title || '法律资料'}.md`, lines.join('\n'))
}
const exportSources = () => {
  const rows = displaySources.value
  if (!rows.length) return ElMessage.warning('当前没有可导出的资料')
  const csv = [['标题', '类型', '状态', '版本', '适用范围', '引用'], ...rows.map((row) => [row.title, sourceTypeLabel(row.source_type), sourceStatusLabel(row.status), row.version || '', row.jurisdiction || '', row.citation || ''])]
    .map((row) => row.map((value) => `"${String(value ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
  downloadText('法律资料搜索结果.csv', `\uFEFF${csv}`, 'text/csv;charset=utf-8')
}

const runSourceSearch = async () => {
  if (libraryMode.value === 'materials') {
    await loadMaterials()
    return
  }
  if (!retrievalQuestion.value.trim()) {
    retrievalResult.value = null
    if (displaySources.value[0]) await openSource(displaySources.value[0])
    return
  }
  await submitRetrievalTest()
  if (displaySources.value[0]) await openSource(displaySources.value[0])
}

onMounted(async () => {
  await loadLegalSources(caseId.value ? { params: { case_id: caseId.value } } : {})
  await loadMaterials()
  if (legalSources.value[0]) await openSource(legalSources.value[0])
})
</script>

<style scoped>
.tab-panel {
  display: grid;
  /* minmax(0, 1fr)：隐式 auto 轨道不会低于子项 min-content（维护表约 880px），
     在 copilot 侧栏占掉 380px 栅格列后会把资料库卡片顶出正文列、被侧栏覆盖。 */
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-5);
}

/* 新建/编辑资料对话框的两列表单栅格。
   原先用 el-row/el-col 实现，但 element-plus 的栅格样式（el-col.css，约 35 kB）
   是整包体积里最贵的一块，这里只需要固定列宽，用原生 grid 替掉。 */
.source-form-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 var(--space-4);
}

.source-form-row.is-three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.source-form-cell {
  min-width: 0;
}

@media (max-width: 720px) {
  .source-form-row,
  .source-form-row.is-three {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* 资料库范围：下划线页签 */
.library-mode-switch { display: flex; gap: var(--space-5); border-bottom: 1px solid var(--color-border); }
.library-mode-switch button { padding: 0 0 var(--space-3); border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-muted); font: inherit; font-size: var(--text-sm); cursor: pointer; }
.library-mode-switch button.active { border-color: var(--color-primary); color: var(--color-primary); font-weight: 600; }

/* 检索条：卡片体里一行放搜索（左）与筛选/导出（右） */
.research-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); flex-wrap: wrap; }
.research-search { display: grid; gap: 6px; flex: 1 1 420px; min-width: 0; }
.research-search label { color: var(--color-text-secondary); font-size: var(--text-xs); font-weight: 600; }
.research-search-row { display: flex; gap: var(--space-2); }
.research-search-row .el-input { flex: 1 1 auto; min-width: 0; }
.research-actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; margin-left: auto; }
.research-filters { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }

/* 两栏骨架：左资料目录 / 右阅读器，两栏等高 */
.research-layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: var(--space-5); min-height: 640px; align-items: stretch; }

/* 资料目录：行沿用 .app-rail-item 的“标题 + 元信息 + 细分隔线”形态；
   .app-rail 的 16px 行距是给 rail 卡片留的，目录行距交给分隔线，保持目录密度 */
.catalog-body { padding: var(--space-2) var(--space-3); }
.source-catalog-rows { gap: 0; }
.source-catalog-row { display: grid; width: 100%; gap: 3px; padding: 11px var(--space-3); border: 0; border-bottom: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; transition: background var(--transition-fast); }
.source-catalog-row:hover { background: var(--color-surface-hover); }
.source-catalog-row.active { background: var(--color-primary-light); }
.source-catalog-row.active .source-catalog-title { color: var(--color-primary-active); }
.source-catalog-title { overflow: hidden; font-size: var(--text-sm); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.source-catalog-meta { display: flex; gap: var(--space-2); color: var(--color-text-muted); font-size: var(--text-xs); }
.source-favorite-mark { color: var(--color-warning); }

/* 阅读器：头部是卡片头，正文列宽沿用原来的 880px 阅读测量 */
.source-reader { min-width: 0; }
.source-reader-header { align-items: flex-start; justify-content: space-between; gap: var(--space-4); row-gap: var(--space-2); flex-wrap: wrap; padding: var(--space-4) var(--space-5); }
.source-reader-heading { display: grid; gap: 3px; min-width: 0; }
.source-reader-kicker { margin: 0; color: var(--color-primary); font-size: var(--text-xs); font-weight: 700; letter-spacing: .04em; }
.source-reader-header h2 { margin: 0; color: var(--color-text); font-size: var(--text-xl); line-height: 1.35; font-weight: 650; }
.source-reader-citation { margin: 0; color: var(--color-text-secondary); font-size: var(--text-sm); }
/* 主栏被 copilot 侧栏挤窄时，状态行换行而不是被卡片溢出裁掉（.app-card 有 overflow:hidden） */
.source-reader-status { display: flex; align-items: center; gap: var(--space-3); row-gap: var(--space-2); flex-wrap: wrap; color: var(--color-text-muted); font-size: var(--text-xs); }
.source-reader-status .el-button + .el-button { margin-left: 0; }
.source-reader-version { font-size: var(--text-xs); white-space: nowrap; }
.source-reader-body { padding: 0; }
.source-facts { display: flex; flex-wrap: wrap; gap: var(--space-5); margin: 0; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--color-border-light); }
.source-facts div { display: grid; gap: 3px; min-width: 130px; }
.source-facts dt { color: var(--color-text-muted); font-size: var(--text-xs); }
.source-facts dd { margin: 0; color: var(--color-text-secondary); font-size: var(--text-sm); }
.source-reader-content { max-width: 880px; padding: var(--space-6) var(--space-5) var(--space-10); }
.source-summary { margin-bottom: var(--space-5); padding-bottom: var(--space-4); border-bottom: 1px solid var(--color-border-light); }
.source-body-label { display: block; margin-bottom: var(--space-2); color: var(--color-text-muted); font-size: var(--text-xs); font-weight: 700; letter-spacing: .04em; }
.source-summary p, .source-article p, .source-full-text { margin: 0; color: var(--color-text); font-size: var(--text-base); line-height: 1.9; white-space: pre-wrap; }
.source-article { padding: var(--space-3) 0; border-bottom: 1px solid var(--color-border-light); }
.source-article-heading { display: flex; gap: var(--space-2); align-items: baseline; margin-bottom: 6px; color: var(--color-text); font-size: var(--text-sm); }
.source-article-heading strong { color: var(--color-primary); font-weight: 650; }
.source-article mark { padding: 0 2px; border-radius: var(--radius-xs); background: var(--color-primary-light); color: var(--color-primary-active); }
.source-reader-empty { display: grid; place-items: center; padding: var(--space-6); color: var(--color-text-muted); font-size: var(--text-sm); }

/* 案件材料：单栏列表卡 */
.material-body { padding: 0; }
.material-library-row { display: grid; grid-template-columns: 68px minmax(0, 1fr) auto; align-items: center; gap: var(--space-4); width: 100%; min-height: 64px; padding: var(--space-3) var(--space-5); border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; transition: background var(--transition-fast); }
.material-library-row:hover { background: var(--color-surface-hover); }
.material-kind, .material-date { color: var(--color-text-muted); font-size: var(--text-xs); }
.material-main { display: grid; gap: 3px; min-width: 0; }
.material-main strong { overflow: hidden; font-size: var(--text-sm); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.material-main small { color: var(--color-text-muted); font-size: var(--text-xs); }
.material-note { display: grid; place-items: center; min-height: 200px; padding: var(--space-6); color: var(--color-text-muted); font-size: var(--text-sm); }

/* 资料维护（折叠区）：导入 / 检索测试 / 资料库 */
.source-maintenance { margin-top: var(--space-2); border-top: 1px solid var(--color-border); }
.source-maintenance > summary { padding: var(--space-3) 2px; color: var(--color-text-secondary); font-size: var(--text-sm); cursor: pointer; list-style-position: inside; }
.source-maintenance > summary span { margin-left: var(--space-2); color: var(--color-text-muted); font-size: var(--text-xs); }
.maintenance-stack { display: grid; gap: var(--space-5); padding-bottom: var(--space-2); }
.import-upload { margin-left: auto; }
.import-format-list { margin: var(--space-2) 0 0 var(--space-5); padding: 0; }
.import-result { margin-top: var(--space-4); padding: var(--space-3); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); background: var(--color-surface-subtle); font-size: var(--text-sm); }
.import-error-block { margin-top: var(--space-3); }
.import-errors { margin: var(--space-1) 0 0 var(--space-5); padding: 0; color: var(--color-danger); }
.summary-text { margin: 0 0 var(--space-3); color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.6; }
.score-breakdown { color: var(--color-text-muted); font-family: var(--font-mono); font-size: var(--text-xs); }
.retrieval-result { margin-top: var(--space-4); }
.maintenance-cta { margin-left: auto; }

@media (max-width: 900px) {
  .research-layout { grid-template-columns: 240px minmax(0, 1fr); }
}
@media (max-width: 640px) {
  .research-layout { grid-template-columns: 1fr; }
  .source-catalog { max-height: 320px; overflow: auto; }
  .source-reader-empty { min-height: 300px; }
  .source-reader-header { flex-direction: column; }
  .research-search { flex: 1 1 100%; }
  .material-library-row { grid-template-columns: 52px minmax(0, 1fr); }
  .material-date { grid-column: 2; }
}
</style>
