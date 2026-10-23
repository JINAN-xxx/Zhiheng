<template>
  <ResourceDrawer title="用户信息详情" :open="visible" width="68%" class="detail-drawer" @close="handleClose">
    <a-spin :spinning="loading">
      <div class="drawer-content">
      <!-- 基本信息 -->
      <h4 class="section-header">基本信息</h4>
      <a-row :gutter="20" class="mb8">
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">用户名称：</label>
            <span class="info-value plaintext">{{ info.nickName }}</span>
          </div>
        </a-col>
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">归属部门：</label>
            <span class="info-value plaintext">{{ (info.dept && info.dept.deptName) }}</span>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="20" class="mb8">
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">手机号码：</label>
            <span class="info-value plaintext">{{ info.phonenumber }}</span>
          </div>
        </a-col>
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">邮箱：</label>
            <span class="info-value plaintext">{{ info.email }}</span>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="20" class="mb8">
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">登录账号：</label>
            <span class="info-value plaintext">{{ info.userName }}</span>
          </div>
        </a-col>
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">用户状态：</label>
            <span class="info-value plaintext">
              <a-tag :color="info.status === '0' ? 'green' : 'red'">{{ info.status === '0' ? '正常' : '停用' }}</a-tag>
            </span>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="20" class="mb8">
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">岗位：</label>
            <span class="info-value plaintext">{{ postNames || '无岗位' }}</span>
          </div>
        </a-col>
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">用户性别：</label>
            <span class="info-value plaintext">{{ sexLabel }}</span>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="20" class="mb8">
        <a-col :span="24">
          <div class="info-item full-width">
            <label class="info-label">角色：</label>
            <span class="info-value plaintext">{{ roleNames || '无角色' }}</span>
          </div>
        </a-col>
      </a-row>
      <!-- 其他信息 -->
      <h4 class="section-header">其他信息</h4>
      <a-row :gutter="20" class="mb8">
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">创建者：</label>
            <span class="info-value plaintext">{{ info.createBy }}</span>
          </div>
        </a-col>
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">创建时间：</label>
            <span class="info-value plaintext">{{ parseTime(info.createTime) }}</span>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="20" class="mb8">
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">更新者：</label>
            <span class="info-value plaintext">{{ info.updateBy }}</span>
          </div>
        </a-col>
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">更新时间：</label>
            <span class="info-value plaintext">{{ parseTime(info.updateTime) }}</span>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="20" class="mb8">
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">最后登录IP：</label>
            <span class="info-value plaintext">{{ info.loginIp }}</span>
          </div>
        </a-col>
        <a-col :xs="24" :md="12">
          <div class="info-item">
            <label class="info-label">最后登录时间：</label>
            <span class="info-value plaintext">{{ parseTime(info.loginDate) }}</span>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="20" class="mb8">
        <a-col :span="24">
          <div class="info-item full-width">
            <label class="info-label">备注：</label>
            <span class="info-value plaintext">{{ info.remark }}</span>
          </div>
        </a-col>
      </a-row>
      </div>
    </a-spin>
  </ResourceDrawer>
</template>

<script setup>
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import { getUser } from '@/api/system/user'

const visible = ref(false)
const loading = ref(false)
const info = reactive({})
const postOptions = ref([])
const roleOptions = ref([])
const { proxy } = getCurrentInstance()

const { sys_user_sex } = proxy.useDict("sys_user_sex")

const sexLabel = computed(() => proxy.selectDictLabel(sys_user_sex.value, info.sex) || '-')

const postNames = computed(() => {
  if (!postOptions.value.length || !info.postIds) return ''
  return postOptions.value.filter(p => info.postIds?.includes(p.postId)).map(p => p.postName).join('、') || ''
})

const roleNames = computed(() => {
  if (!roleOptions.value.length || !info.roleIds) return ''
  return roleOptions.value.filter(r => info.roleIds?.includes(r.roleId)).map(r => r.roleName).join('、') || ''
})

const open = async (userId) => {
  visible.value = true
  loading.value = true
  try {
    const res = await getUser(userId)
    Object.assign(info, res.data || {})
    postOptions.value = res.posts || []
    roleOptions.value = res.roles || []
    info.postIds = res.postIds || []
    info.roleIds = res.roleIds || []
  } catch (error) {
    console.error('获取用户信息失败:', error)
  } finally {
    loading.value = false
  }
}

function handleClose() {
  visible.value = false
}

defineExpose({
  open
})
</script>
