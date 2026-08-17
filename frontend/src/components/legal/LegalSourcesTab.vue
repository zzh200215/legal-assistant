<template>
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
</template>

<script setup>
import { onMounted } from 'vue'
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
import { Upload } from '@element-plus/icons-vue'
import legalWorkspace from '../../api/legalWorkspace'
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

onMounted(() => {
  loadLegalSources()
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
</style>
