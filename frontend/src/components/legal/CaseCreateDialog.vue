<template>
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
</template>

<script setup>
import { ref } from 'vue'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import { ElMessage } from 'element-plus/es/components/message/index'
import legalWorkspace from '../../api/legalWorkspace'

const props = defineProps({
  orgId: { type: Number, default: 1 },
})
const emit = defineEmits(['created'])

const caseDialogVisible = ref(false)
const caseCreating = ref(false)
const caseForm = ref({ title: '', case_type: 'labor_dispute', description: '' })

const createCase = async () => {
  if (!caseForm.value.title.trim()) return ElMessage.warning('请输入案件名称')
  caseCreating.value = true
  try {
    const { data } = await legalWorkspace.createCase(props.orgId, {
      ...caseForm.value, organization_id: props.orgId,
    })
    ElMessage.success(`案件 #${data.id} 已创建`)
    caseDialogVisible.value = false
    emit('created', data.id)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '创建失败')
  } finally {
    caseCreating.value = false
  }
}

defineExpose({
  open() {
    caseForm.value = { title: '', case_type: 'labor_dispute', description: '' }
    caseDialogVisible.value = true
  },
})
</script>
