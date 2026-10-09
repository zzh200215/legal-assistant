<template>
        <div class="tab-panel">
          <div class="library-mode-switch" role="tablist" aria-label="资料库范围">
            <button type="button" :class="{ active: libraryMode === 'sources' }" @click="libraryMode = 'sources'">法规与案例</button>
            <button type="button" :class="{ active: libraryMode === 'materials' }" @click="libraryMode = 'materials'">案件材料</button>
          </div>
          <section class="research-library">
            <div class="research-toolbar">
              <div class="research-search">
                <label for="legal-source-search">搜索资料</label>
                <div class="research-search-row">
                <el-input id="legal-source-search" v-model="retrievalQuestion" :placeholder="libraryMode === 'sources' ? '搜索法规、案例、合同模板或文书模板' : '搜索案件材料标题'" clearable @keyup.enter="runSourceSearch" />
                  <el-button type="primary" :loading="retrievalLoading || materialLoading" @click="runSourceSearch">搜索</el-button>
              </div>
            </div>
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

            <div v-if="libraryMode === 'sources'" class="research-layout">
              <aside class="source-catalog" aria-label="资料目录">
                <div class="catalog-heading"><strong>资料目录</strong><span>{{ displaySources.length }} 条</span></div>
                  <button
                  v-for="source in displaySources"
                  :key="source.id"
                  type="button"
                  class="source-catalog-row"
                  :class="{ active: selectedSource?.id === source.id }"
                  @click="openSource(source)"
                  >
                    <span class="source-catalog-title">{{ source.title }}</span>
                    <span class="source-catalog-meta"><span>{{ sourceTypeLabel(source.source_type) }}</span><span>{{ sourceStatusLabel(source.status) }}</span><span v-if="source.is_favorite" class="source-favorite-mark" title="已收藏">★</span><span v-if="source.is_linked" title="已关联当前案件">案件</span></span>
                </button>
                <div v-if="!displaySources.length" class="legal-empty">没有符合条件的资料。</div>
              </aside>

              <article v-if="selectedSource" class="source-reader" aria-label="资料阅读器">
                <header class="source-reader-header">
                  <div>
                    <p class="source-reader-kicker">{{ sourceTypeLabel(selectedSource.source_type) }}</p>
                    <h2>{{ selectedSource.title }}</h2>
                    <p v-if="selectedSource.citation" class="source-reader-citation">{{ selectedSource.citation }}</p>
                  </div>
                  <div class="source-reader-status">
                    <el-tag :type="sourceStatusType(selectedSource.status)" size="small">{{ sourceStatusLabel(selectedSource.status) }}</el-tag>
                    <span v-if="selectedSource.version">版本 {{ selectedSource.version }}</span>
                    <el-button text size="small" :type="selectedSource.is_favorite ? 'warning' : 'default'" @click="toggleFavorite(selectedSource)"><el-icon><Star /></el-icon>{{ selectedSource.is_favorite ? '已收藏' : '收藏' }}</el-button>
                    <el-button v-if="caseId" text size="small" :type="selectedSource.is_linked ? 'success' : 'default'" @click="toggleCaseLink(selectedSource)"><el-icon><Link /></el-icon>{{ selectedSource.is_linked ? '已关联' : '关联案件' }}</el-button>
                    <el-button text size="small" @click="exportSource(selectedSource)"><el-icon><Download /></el-icon>导出</el-button>
                  </div>
                </header>

                <dl class="source-facts">
                  <div v-if="selectedSource.promulgator"><dt>发布机关</dt><dd>{{ selectedSource.promulgator }}</dd></div>
                  <div v-if="selectedSource.document_number"><dt>发文字号</dt><dd>{{ selectedSource.document_number }}</dd></div>
                  <div v-if="selectedSource.jurisdiction"><dt>适用范围</dt><dd>{{ selectedSource.jurisdiction }}</dd></div>
                  <div v-if="selectedSource.effective_date"><dt>生效日期</dt><dd>{{ selectedSource.effective_date }}</dd></div>
                  <div v-if="selectedSource.expiration_date"><dt>失效日期</dt><dd>{{ selectedSource.expiration_date }}</dd></div>
                  <div v-if="selectedSource.applicability_scope"><dt>适用事项</dt><dd>{{ selectedSource.applicability_scope }}</dd></div>
                </dl>

                <div class="source-reader-body">
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
              </article>
              <div v-else class="source-reader-empty">从左侧选择一份资料开始阅读。</div>
            </div>
            <div v-else class="material-library">
              <div v-if="materialLoading" class="source-reader-empty">正在加载案件材料…</div>
              <button v-for="item in filteredMaterials" :key="item.id" type="button" class="material-library-row" @click="openMaterial(item)">
                <span class="material-kind">{{ documentKindLabel(item) }}</span>
                <span class="material-main"><strong>{{ item.title }}</strong><small>{{ item.file_type || '文档' }} · v{{ item.version_number || 1 }} · {{ item.status || '处理中' }}</small></span>
                <span class="material-date">{{ formatDate(item.created_at) }}</span>
              </button>
              <div v-if="!materialLoading && !filteredMaterials.length" class="source-reader-empty">当前没有符合条件的案件材料。</div>
            </div>
          </section>

          <details class="source-maintenance">
            <summary>资料维护 <span>导入、编辑与状态管理</span></summary>
          <el-card shadow="never">
            <template #header>
              <div class="result-header">
                <span class="card-title">资料导入</span>
                <el-upload
                  :before-upload="handleSourceImport"
                  :show-file-list="false"
                  accept=".csv,.xlsx,.xls"
                  style="margin-left:auto"
                >
                  <el-button type="primary" size="small" :loading="importLoading">
                    <el-icon><Upload /></el-icon>
                    导入资料表
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
            <template #header><span class="card-title">资料搜索</span></template>
            <el-form @submit.prevent="submitRetrievalTest">
                <el-form-item label="搜索资料">
                <el-input v-model="retrievalQuestion" placeholder="搜索法规、案例、合同模板或文书模板..." @keyup.enter="submitRetrievalTest" />
              </el-form-item>
              <el-button type="primary" :loading="retrievalLoading" @click="submitRetrievalTest">搜索</el-button>
            </el-form>

            <div v-if="retrievalResult" style="margin-top:16px">
              <p class="summary-text">共 {{ retrievalResult.total_sources }} 条资料，找到 {{ retrievalResult.results.filter(r => r.total_score > 0).length }} 条相关结果</p>
              <el-table :data="retrievalResult.results" stripe size="small" max-height="400">
                <el-table-column prop="title" label="法源名称" show-overflow-tooltip />
                <el-table-column prop="total_score" label="相关性" width="90" sortable>
                  <template #default="{ row }">
                    <el-tag :type="row.total_score > 0 ? 'success' : 'info'" size="small">{{ row.total_score }}</el-tag>
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
                <span class="card-title">资料库</span>
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

          <el-dialog v-model="sourceDialogVisible" :title="editingSource ? '编辑资料' : '新建资料'" width="680px">
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
          </details>
        </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElUpload } from 'element-plus/es/components/upload/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElCheckbox } from 'element-plus/es/components/checkbox/index'
import 'element-plus/es/components/upload/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/checkbox/style/css'
import { Upload } from '@element-plus/icons-vue'
import { Download, Link, Star } from '@element-plus/icons-vue'
import legalWorkspace from '../../api/legalWorkspace'
import api from '../../api'
import { useLegalSources } from '../../composables/useLegalSources'
import { sourceStatusLabel, sourceStatusType, sourceTypeLabel } from '../../composables/useLegalWorkspacePresentation'

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
  gap: 20px;
}
.library-mode-switch { display: flex; gap: 22px; border-bottom: 1px solid var(--color-border); }
.library-mode-switch button { padding: 0 0 10px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-muted); font: inherit; font-size: 14px; cursor: pointer; }
.library-mode-switch button.active { border-color: var(--color-primary); color: var(--color-primary); font-weight: 600; }
.card-title {
  font-weight: 700;
  font-size: 15px;
}
.result-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.summary-text {
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.6;
  margin: 0 0 12px;
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
.research-library { border: 1px solid var(--color-border); background: var(--color-surface); }
.research-toolbar { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; padding: 18px 20px; border-bottom: 1px solid var(--color-border); }
.research-search { display: grid; gap: 7px; min-width: min(620px, 100%); }
.research-search label { color: var(--color-text-secondary); font-size: 12px; font-weight: 600; }
.research-search-row { display: flex; gap: 8px; }
.research-search-row .el-input { min-width: min(480px, 60vw); }
.research-filters { display: flex; gap: 8px; }
.research-layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); min-height: 640px; }
.source-catalog { border-right: 1px solid var(--color-border); background: #FBFCFD; }
.catalog-heading { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; border-bottom: 1px solid var(--color-border-light); color: var(--color-text); font-size: 13px; }
.catalog-heading span { color: var(--color-text-muted); font-size: 11px; font-weight: 400; }
.source-catalog-row { display: grid; width: 100%; gap: 6px; padding: 13px 16px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.source-catalog-row:hover { background: var(--color-surface-hover); }
.source-catalog-row.active { background: var(--color-primary-light); box-shadow: inset 3px 0 var(--color-primary); }
.source-catalog-title { overflow: hidden; font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.source-catalog-meta { display: flex; gap: 10px; color: var(--color-text-muted); font-size: 11px; }
.source-favorite-mark { color: #B7791F; }
.source-reader { min-width: 0; background: #fff; }
.source-reader-header { display: flex; justify-content: space-between; gap: 20px; padding: 24px 30px 18px; border-bottom: 1px solid var(--color-border-light); }
.source-reader-kicker { margin: 0 0 5px; color: var(--color-primary); font-size: 11px; font-weight: 600; }
.source-reader-header h2 { margin: 0; color: var(--color-text); font-size: 21px; font-weight: 620; line-height: 1.4; }
.source-reader-citation { margin: 6px 0 0; color: var(--color-text-secondary); font-size: 13px; }
.source-reader-status { display: flex; align-items: flex-start; gap: 12px; color: var(--color-text-muted); font-size: 11px; white-space: nowrap; }
.source-facts { display: flex; flex-wrap: wrap; gap: 20px; margin: 0; padding: 14px 30px; border-bottom: 1px solid var(--color-border-light); }
.source-facts div { display: grid; gap: 3px; min-width: 130px; }
.source-facts dt { color: var(--color-text-muted); font-size: 11px; }
.source-facts dd { margin: 0; color: var(--color-text-secondary); font-size: 12px; }
.source-reader-body { max-width: 880px; padding: 24px 30px 40px; }
.source-summary { margin-bottom: 24px; padding-bottom: 18px; border-bottom: 1px solid var(--color-border-light); }
.source-body-label { display: block; margin-bottom: 9px; color: var(--color-text-muted); font-size: 11px; font-weight: 600; letter-spacing: .04em; }
.source-summary p, .source-article p, .source-full-text { margin: 0; color: #293542; font-size: 14px; line-height: 1.9; white-space: pre-wrap; }
.source-article { padding: 14px 0; border-bottom: 1px solid var(--color-border-light); }
.source-article-heading { display: flex; gap: 10px; align-items: baseline; margin-bottom: 7px; color: var(--color-text); font-size: 13px; }
.source-article-heading strong { color: var(--color-primary); font-weight: 650; }
.source-article mark { background: #FFE29A; color: inherit; }
.source-reader-empty { display: grid; place-items: center; min-height: 640px; color: var(--color-text-muted); font-size: 13px; }
.material-library { min-height: 640px; background: #fff; }
.material-library-row { display: grid; grid-template-columns: 68px minmax(0, 1fr) auto; align-items: center; gap: 16px; width: 100%; min-height: 64px; padding: 12px 24px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.material-library-row:hover { background: #F8FAFB; }
.material-kind, .material-date { color: var(--color-text-muted); font-size: 11px; }
.material-main { display: grid; gap: 4px; min-width: 0; }
.material-main strong { overflow: hidden; font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.material-main small { color: var(--color-text-muted); font-size: 11px; }
.source-maintenance { margin-top: 14px; border-top: 1px solid var(--color-border); }
.source-maintenance > summary { padding: 14px 2px; color: var(--color-text-secondary); font-size: 12px; cursor: pointer; list-style-position: inside; }
.source-maintenance > summary span { margin-left: 8px; color: var(--color-text-muted); }
@media (max-width: 900px) { .research-toolbar { align-items: stretch; flex-direction: column; } .research-search { min-width: 0; } .research-search-row .el-input { min-width: 0; flex: 1; } .research-layout { grid-template-columns: 240px minmax(0,1fr); } .source-reader-header, .source-facts, .source-reader-body { padding-left: 20px; padding-right: 20px; } }
@media (max-width: 640px) { .research-layout { grid-template-columns: 1fr; } .source-catalog { border-right: 0; border-bottom: 1px solid var(--color-border); max-height: 260px; overflow: auto; } .source-reader-empty { min-height: 300px; } .source-reader-header { flex-direction: column; } .source-reader-status { flex-wrap: wrap; white-space: normal; } .research-filters { flex-wrap: wrap; } .material-library-row { grid-template-columns: 52px minmax(0, 1fr); padding-left: 16px; padding-right: 16px; } .material-date { grid-column: 2; } }
</style>
