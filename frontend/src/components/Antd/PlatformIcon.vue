<template>
  <component :is="iconComponent" aria-hidden="true" />
</template>

<script setup>
import { computed } from 'vue'
import {
  AppstoreOutlined,
  BellOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  EditOutlined,
  FileOutlined,
  FundProjectionScreenOutlined,
  HomeOutlined,
  MenuOutlined,
  MessageOutlined,
  QuestionOutlined,
  RobotOutlined,
  SearchOutlined,
  SettingOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'

import { resolvePlatformIcon } from './componentContracts'

const props = defineProps({
  name: { type: String, default: '' },
})

const components = Object.freeze({
  AppstoreOutlined,
  BellOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  EditOutlined,
  FileOutlined,
  FundProjectionScreenOutlined,
  HomeOutlined,
  MenuOutlined,
  MessageOutlined,
  QuestionOutlined,
  RobotOutlined,
  SearchOutlined,
  SettingOutlined,
  ToolOutlined,
  UserOutlined,
})

const iconComponent = computed(() => components[resolvePlatformIcon(props.name)] ?? QuestionOutlined)
</script>
