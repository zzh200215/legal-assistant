<template>
  <template v-if="!isAdmin">
    <el-card class="system-panel-card">
      <div class="app-readonly-banner">
        <strong>仅管理员可管理组织架构</strong>
        <span>当前账号只能查看组织归属信息，不能创建组织、部门或修改用户归属。</span>
      </div>
    </el-card>
  </template>
  <template v-else>
    <div class="app-section-intro tab-intro">
      <strong>组织、部门与归属管理</strong>
      <span>维护组织结构、部门清单和用户归属，为权限与共享范围提供基础数据。</span>
    </div>

    <el-row :gutter="16" class="system-block-row">
      <el-col :span="12">
        <el-card>
          <template #header>创建组织</template>
          <div style="display: grid; gap: 12px">
            <el-input v-model="newOrg.name" placeholder="组织名称" />
            <el-input v-model="newOrg.code" placeholder="组织编码" />
            <el-input v-model="newOrg.description" placeholder="组织说明" />
            <el-button type="primary" :loading="orgLoading" @click="createOrganization">创建组织</el-button>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header>创建部门</template>
          <div style="display: grid; gap: 12px">
            <el-select v-model="newDepartment.organization_id" placeholder="选择组织">
              <el-option v-for="item in organizations" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <el-input v-model="newDepartment.name" placeholder="部门名称" />
            <el-input v-model="newDepartment.code" placeholder="部门编码" />
            <el-input v-model="newDepartment.description" placeholder="部门说明" />
            <el-button type="primary" :loading="orgLoading" @click="createDepartment">创建部门</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="system-block-row">
      <el-col :span="10">
        <el-card>
          <template #header>组织列表</template>
          <el-table :data="organizations" v-loading="orgLoading" border size="small" max-height="420">
            <el-table-column prop="name" label="名称" />
            <el-table-column prop="code" label="编码" width="120" />
            <el-table-column prop="description" label="说明" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card>
          <template #header>部门列表</template>
          <el-table :data="departments" v-loading="orgLoading" border size="small" max-height="420">
            <el-table-column prop="organization_id" label="组织 ID" width="90" />
            <el-table-column prop="name" label="部门名称" />
            <el-table-column prop="code" label="编码" width="120" />
            <el-table-column prop="description" label="说明" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="system-block-row">
      <el-col :span="10">
        <el-card>
          <template #header>用户归属分配</template>
          <div style="display: grid; gap: 12px">
            <el-select v-model="userAssignForm.user_id" placeholder="选择用户">
              <el-option v-for="item in users" :key="item.id" :label="`${item.username} (${item.role})`" :value="item.id" />
            </el-select>
            <el-select v-model="userAssignForm.organization_id" placeholder="选择组织">
              <el-option v-for="item in organizations" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <el-select v-model="userAssignForm.department_id" placeholder="选择部门">
              <el-option v-for="item in departments.filter((row) => !userAssignForm.organization_id || row.organization_id === userAssignForm.organization_id)" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <el-input v-model="userAssignForm.job_title" placeholder="岗位名称" />
            <el-button type="primary" :loading="orgLoading" @click="assignUserOrg">保存归属</el-button>
          </div>
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card>
          <template #header>用户列表</template>
          <el-table :data="users" v-loading="orgLoading" border size="small" max-height="420">
            <el-table-column prop="username" label="用户名" />
            <el-table-column prop="role" label="角色" width="100" />
            <el-table-column prop="organization_id" label="组织 ID" width="90" />
            <el-table-column prop="department_id" label="部门 ID" width="90" />
            <el-table-column prop="job_title" label="岗位" width="120" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </template>
</template>

<script setup>
import { computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemOrganization } from '../../composables/useSystemOrganization'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  orgLoading,
  organizations,
  departments,
  users,
  newOrg,
  newDepartment,
  userAssignForm,
  fetchOrgData,
  createOrganization,
  createDepartment,
  assignUserOrg,
} = useSystemOrganization({ client: api, message: ElMessage })

watch(isAdmin, (admin) => {
  if (admin) fetchOrgData()
}, { immediate: true })
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.system-block-row {
  margin-top: var(--space-4);
  margin-bottom: 0;
}
</style>
