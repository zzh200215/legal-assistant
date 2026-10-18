<template>
  <template v-if="!isAdmin">
    <div class="app-readonly-banner system-panel-card">
      <strong>仅管理员可管理组织架构</strong>
      <span>当前账号可以查看组织归属信息，不能创建组织、部门或调整用户归属。</span>
    </div>
  </template>
  <template v-else>
    <div class="app-section-intro tab-intro">
      <strong>组织、部门与归属管理</strong>
      <span>维护组织结构、部门清单和用户归属，为权限与共享范围提供基础数据。</span>
    </div>

    <!-- 下面三张表单卡保留 el-card：e2e（system-orgs.spec.js）用 .el-card + hasText
         「创建组织 / 创建部门 / 用户归属分配」精确定位，换成 .app-card 会打断断言 -->
    <div class="system-block-row">
      <div class="system-block-cell">
        <el-card>
          <template #header>创建组织</template>
          <div style="display: grid; gap: 12px">
            <el-input v-model="newOrg.name" placeholder="组织名称" />
            <el-input v-model="newOrg.code" placeholder="组织编码" />
            <el-input v-model="newOrg.description" placeholder="组织说明" />
            <el-button type="primary" :loading="orgLoading" @click="createOrganization">创建组织</el-button>
          </div>
        </el-card>
      </div>
      <div class="system-block-cell">
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
      </div>
    </div>

    <div class="system-block-row is-split">
      <div class="system-block-cell">
        <section class="app-card org-list-card">
          <div class="app-card-head">
            <h3>组织列表</h3>
          </div>
          <div class="app-card-body">
            <QueryStateView :status="orgStatus" :error="orgError" empty-text="暂无组织" min-height="200px" @retry="fetchOrgData">
            <el-table v-loading="orgLoading" :data="organizations" border size="small" max-height="420">
              <el-table-column prop="name" label="名称" />
              <el-table-column prop="code" label="编码" width="120" />
              <el-table-column prop="description" label="说明" show-overflow-tooltip />
            </el-table>
            </QueryStateView>
          </div>
        </section>
      </div>
      <div class="system-block-cell">
        <section class="app-card org-list-card">
          <div class="app-card-head">
            <h3>部门列表</h3>
          </div>
          <div class="app-card-body">
            <el-table v-loading="orgLoading" :data="departments" border size="small" max-height="420">
              <el-table-column prop="organization_id" label="组织 ID" width="90" />
              <el-table-column prop="name" label="部门名称" />
              <el-table-column prop="code" label="编码" width="120" />
              <el-table-column prop="description" label="说明" show-overflow-tooltip />
            </el-table>
          </div>
        </section>
      </div>
    </div>

    <div class="system-block-row is-split">
      <div class="system-block-cell">
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
      </div>
      <div class="system-block-cell">
        <section class="app-card org-list-card">
          <div class="app-card-head">
            <h3>用户列表</h3>
          </div>
          <div class="app-card-body">
            <el-table v-loading="orgLoading" :data="users" border size="small" max-height="420">
              <el-table-column prop="username" label="用户名" />
              <el-table-column prop="role" label="角色" width="100" />
              <el-table-column prop="organization_id" label="组织 ID" width="90" />
              <el-table-column prop="department_id" label="部门 ID" width="90" />
              <el-table-column prop="job_title" label="岗位" width="120" />
            </el-table>
          </div>
        </section>
      </div>
    </div>
  </template>
</template>

<script setup>
import { computed, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemOrganization } from '../../composables/useSystemOrganization'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  orgLoading,
  organizations,
  orgError,
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

// 三处（ux-audit M-13）：仅包裹主结果表 organizations；三表共用一次 fetch，保留原有 message.error 以覆盖其它表
const orgStatus = computed(() => {
  if (orgLoading.value && !organizations.value.length) return 'loading'
  if (orgError.value) return 'error'
  if (!organizations.value.length) return 'empty'
  return 'success'
})

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
  display: grid;
  /* 两列等宽；is-split 为 5:7（对应原先 el-col 的 10/14 栅格） */
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-4);
  margin-bottom: 0;
  /* 卡片各自贴合内容高度，避免矮表被同行高表单拉出大片空白 */
  align-items: start;
}
.system-block-row.is-split {
  grid-template-columns: minmax(0, 5fr) minmax(0, 7fr);
}
.system-block-cell {
  min-width: 0;
}
@media (max-width: 900px) {
  .system-block-row,
  .system-block-row.is-split {
    grid-template-columns: minmax(0, 1fr);
  }
}
/* 只读横幅不再包在卡片里（避免卡中卡），这里补上与 .app-section-intro 一致的堆叠排版 */
.app-readonly-banner {
  display: grid;
  gap: 6px;
}
</style>
