<template>
  <ResourceDrawer
    title="文件保留策略"
    :open="visible"
    :width="900"
    @close="visible = false"
  >
    <a-alert
      message="策略仅应用于后续新建或重建的业务引用；未配置或已停用的业务类型永久保留，既有引用不会被追溯修改。"
      type="info"
      :closable="false"
      show-icon
      class="mb8"
    />
    <div class="mb8">
        <a-button type="primary" @click="handleAdd"><PlusOutlined />
          新增
        </a-button>
    </div>
    <ResourceTable :columns="columns" :rows="policyList" :loading="loading" :pagination="false" row-key="businessType">
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'status'"><a-tag :color="row.status === '0' ? 'green' : 'default'">{{ row.status === '0' ? '启用' : '停用' }}</a-tag></template>
        <template v-else-if="column.key === 'remark'">{{ row.remark || '-' }}</template>
        <template v-else-if="column.key === 'updateTime'">{{ parseTime(row.updateTime) || '-' }}</template>
        <a-space v-else-if="column.key === 'actions'"><a-tooltip title="修改"><a-button type="link" @click="handleEdit(row)"><EditOutlined /></a-button></a-tooltip><a-tooltip title="删除"><a-button type="link" danger @click="handleDelete(row)"><DeleteOutlined /></a-button></a-tooltip></a-space>
      </template>
    </ResourceTable>
  </ResourceDrawer>

  <a-modal
    :title="formTitle"
    v-model:open="formOpen"
    :width="520"
  >
    <a-form
      ref="formRef"
      :model="form"
      :rules="rules"
      layout="vertical"
    >
      <a-form-item label="业务类型" name="businessType">
        <a-input
          v-model:value="form.businessType"
          :disabled="editing"
          maxlength="50"
          placeholder="请输入业务类型"
        />
      </a-form-item>
      <a-form-item label="保留天数" name="retentionDays">
        <a-input-number
          v-model:value="form.retentionDays"
          :min="1"
          :max="36500"
          :precision="0"
          style="width: 100%"
        />
      </a-form-item>
      <a-form-item label="状态" name="status"><a-radio-group v-model:value="form.status" :options="statusOptions" /></a-form-item>
      <a-form-item label="备注" name="remark">
        <a-textarea
          v-model:value="form.remark"
          :rows="3"
          :maxlength="500"
          show-count
          placeholder="请输入备注"
        />
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="dialog-footer">
        <a-button type="primary" :loading="saving" @click="submit">
          确 定
        </a-button>
        <a-button @click="formOpen = false">取 消</a-button>
      </div>
    </template>
  </a-modal>
</template>

<script setup>
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import feedback from '@/plugins/antdFeedback'
import {
  addFileRetentionPolicy,
  delFileRetentionPolicy,
  listFileRetentionPolicy,
  updateFileRetentionPolicy
} from "@/api/system/file";

const { proxy } = getCurrentInstance();
const visible = ref(false);
const loading = ref(false);
const policyList = ref([]);
const formOpen = ref(false);
const editing = ref(false);
const saving = ref(false);
const formRef = ref();
const formTitle = computed(() =>
  editing.value ? "修改文件保留策略" : "新增文件保留策略"
);
const form = reactive({
  businessType: undefined,
  retentionDays: 30,
  status: "0",
  remark: undefined
});
const rules = {
  businessType: [{ required: true, message: "请输入业务类型", trigger: "blur" }],
  retentionDays: [{ required: true, message: "请输入保留天数", trigger: "blur" }]
};
const statusOptions = [{label:'启用',value:'0'},{label:'停用',value:'1'}];
const columns = [{title:'业务类型',dataIndex:'businessType',key:'businessType'},{title:'保留天数',dataIndex:'retentionDays',key:'retentionDays',width:100},{title:'状态',key:'status',width:90},{title:'备注',key:'remark',ellipsis:true},{title:'更新时间',key:'updateTime',width:180},{title:'操作',key:'actions',width:100}];

function open() {
  visible.value = true;
  getList();
}

function getList() {
  loading.value = true;
  listFileRetentionPolicy()
    .then(response => {
      policyList.value = response.data;
    })
    .finally(() => {
      loading.value = false;
    });
}

function resetForm() {
  Object.assign(form, {
    businessType: undefined,
    retentionDays: 30,
    status: "0",
    remark: undefined
  });
  nextTick(() => formRef.value?.clearValidate());
}

function handleAdd() {
  editing.value = false;
  resetForm();
  formOpen.value = true;
}

function handleEdit(row) {
  editing.value = true;
  Object.assign(form, {
    businessType: row.businessType,
    retentionDays: row.retentionDays,
    status: row.status,
    remark: row.remark
  });
  formOpen.value = true;
  nextTick(() => formRef.value?.clearValidate());
}

function submit() {
  formRef.value.validateFields().then(() => {
    saving.value = true;
    const submitRequest = editing.value
      ? updateFileRetentionPolicy(form)
      : addFileRetentionPolicy(form);
    submitRequest
      .then(() => {
        feedback.msgSuccess(editing.value ? "修改成功" : "新增成功");
        formOpen.value = false;
        getList();
      })
      .finally(() => {
        saving.value = false;
      });
  }).catch(() => {});
}

function handleDelete(row) {
  feedback
    .confirm(`是否确认删除业务类型“${row.businessType}”的保留策略?`)
    .then(() => delFileRetentionPolicy(row.businessType))
    .then(() => {
      feedback.msgSuccess("删除成功");
      getList();
    })
    .catch(() => {});
}

defineExpose({ open });
</script>
