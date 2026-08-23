<template>
  <ResourceDrawer
    :title="`业务引用 - ${fileName}`"
    :open="visible"
    width="55%"
    @close="closeDrawer"
  >
    <a-alert
      message="存在业务引用时文件不能移入回收站，请先在对应业务中解除文件引用。"
      type="warning"
      :closable="false"
      show-icon
      class="mb8"
    />
    <ResourceTable :columns="columns" :rows="referenceList" :loading="loading" :pagination="false" row-key="referenceId" :scroll="{ x: 900 }">
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'businessName'">{{ row.businessName || "-" }}</template>
        <template v-else-if="column.key === 'retentionExpireTime'">
          {{
            row.retentionExpireTime
              ? parseTime(row.retentionExpireTime)
              : "永久保留"
          }}
        </template>
        <template v-else-if="column.key === 'source'">
          <a-tag v-if="row.legacy">
            兼容字段
          </a-tag>
          <a-tag v-else color="green">引用关系</a-tag>
        </template>
        <template v-else-if="column.key === 'createBy'">{{ row.createBy || "-" }}</template>
        <template v-else-if="column.key === 'createTime'">
          {{ parseTime(row.createTime) || "-" }}
        </template>
      </template>
    </ResourceTable>
  </ResourceDrawer>
</template>

<script setup>
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import { listFileReference } from "@/api/system/file";

const visible = ref(false);
const loading = ref(false);
const fileName = ref("");
const fileId = ref("");
const referenceList = ref([]);
let requestGeneration = 0;
const columns = [
  { title: '业务类型', dataIndex: 'businessType', key: 'businessType', width: 130, ellipsis: true },
  { title: '业务名称', dataIndex: 'businessName', key: 'businessName', width: 180, ellipsis: true },
  { title: '业务ID', dataIndex: 'businessId', key: 'businessId', width: 160, ellipsis: true },
  { title: '保留到期', key: 'retentionExpireTime', width: 180 },
  { title: '来源', key: 'source', width: 100 },
  { title: '登记人', key: 'createBy', width: 110 },
  { title: '登记时间', key: 'createTime', width: 180 },
];

function open(row) {
  const generation = ++requestGeneration;
  const requestFileId = row.fileId;
  fileId.value = requestFileId;
  fileName.value = row.originalName;
  referenceList.value = [];
  visible.value = true;
  loading.value = true;
  listFileReference(requestFileId)
    .then(response => {
      if (generation !== requestGeneration || !visible.value || requestFileId !== fileId.value) return;
      referenceList.value = response.data;
    })
    .finally(() => {
      if (generation === requestGeneration) loading.value = false;
    });
}

function closeDrawer() { requestGeneration += 1; visible.value = false; loading.value = false; referenceList.value = []; }

defineExpose({ open });
onBeforeUnmount(() => { requestGeneration += 1 })
</script>
