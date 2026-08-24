<template>
  <el-drawer v-model="showSettings" :with-header="false" :lock-scroll="false" direction="rtl" size="320px">
    <div class="settings-heading">
      <span>界面设置</span>
      <strong>工作台外观</strong>
    </div>

    <div class="drawer-item">
      <span>深色模式</span>
      <el-switch v-model="darkMode" />
    </div>

    <div class="drawer-item">
      <span>界面密度</span>
      <el-segmented v-model="interfaceSize" :options="sizeOptions" size="small" />
    </div>

    <el-divider />

    <div class="drawer-item">
      <span>固定顶部</span>
      <el-switch v-model="settingsStore.fixedHeader" />
    </div>

    <div class="drawer-item">
      <span>动态标题</span>
      <el-switch v-model="settingsStore.dynamicTitle" @change="dynamicTitleChange" />
    </div>

    <div class="settings-actions">
      <el-button type="primary" plain icon="DocumentAdd" @click="saveSetting">保存配置</el-button>
      <el-button plain icon="Refresh" @click="resetSetting">重置配置</el-button>
    </div>
  </el-drawer>
</template>

<script setup>
import useAppStore from '@/store/modules/app'
import useSettingsStore from '@/store/modules/settings'

const { proxy } = getCurrentInstance()
const appStore = useAppStore()
const settingsStore = useSettingsStore()
const showSettings = ref(false)
const sizeOptions = [
  { label: '紧凑', value: 'small' },
  { label: '标准', value: 'default' },
  { label: '宽松', value: 'large' },
]

const darkMode = computed({
  get: () => settingsStore.isDark,
  set: (value) => {
    if (value !== settingsStore.isDark) settingsStore.toggleTheme()
  },
})

const interfaceSize = computed({
  get: () => appStore.size,
  set: (value) => appStore.setSize(value),
})

function dynamicTitleChange() {
  settingsStore.setTitle(settingsStore.title)
}

function saveSetting() {
  proxy.$modal.loading('正在保存到本地，请稍候...')
  localStorage.setItem('layout-setting', JSON.stringify({
    fixedHeader: settingsStore.fixedHeader,
    dynamicTitle: settingsStore.dynamicTitle,
  }))
  setTimeout(() => proxy.$modal.closeLoading(), 300)
}

function resetSetting() {
  proxy.$modal.loading('正在清除设置缓存并刷新，请稍候...')
  localStorage.removeItem('layout-setting')
  setTimeout(() => window.location.reload(), 300)
}

function openSetting() {
  showSettings.value = true
}

defineExpose({ openSetting })
</script>

<style scoped lang="scss">
.settings-heading {
  display: grid;
  gap: 3px;
  margin-bottom: 22px;
  color: var(--workspace-text);
}

.settings-heading span {
  color: var(--workspace-main-deep);
  font-size: 11px;
  font-weight: 700;
}

.settings-heading strong {
  font-size: 18px;
}

.drawer-item {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: var(--workspace-text-secondary);
  font-size: 14px;
}

.settings-actions {
  display: flex;
  margin-top: 24px;
}
</style>
