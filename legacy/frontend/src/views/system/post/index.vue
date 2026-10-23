<template>
  <WorkspacePage title="岗位管理" subtitle="岗位、编码与启停状态">
    <template #actions>
      <a-space wrap>
        <a-button type="primary" @click="handleAdd" v-hasPermi="['system:post:add']"><PlusOutlined />新增</a-button>
        <a-button :disabled="single" @click="handleUpdate" v-hasPermi="['system:post:edit']"><EditOutlined />修改</a-button>
        <a-button danger :disabled="multiple" @click="handleDelete" v-hasPermi="['system:post:remove']"><DeleteOutlined />删除</a-button>
        <a-button @click="handleExport" v-hasPermi="['system:post:export']"><DownloadOutlined />导出</a-button>
        <a-tooltip title="显示/隐藏搜索"><a-button shape="circle" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip>
        <a-tooltip title="刷新"><a-button shape="circle" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
      </a-space>
    </template>

    <a-form v-show="showSearch" ref="queryRef" :model="queryParams" layout="inline" class="query-form">
      <a-form-item label="岗位编码" name="postCode"><a-input v-model:value="queryParams.postCode" allow-clear placeholder="请输入岗位编码" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="岗位名称" name="postName"><a-input v-model:value="queryParams.postName" allow-clear placeholder="请输入岗位名称" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="状态" name="status"><a-select v-model:value="queryParams.status" allow-clear placeholder="岗位状态" :options="normalDisableOptions" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>

    <ResourceTable :columns="columns" :rows="postList" :loading="loading" :pagination="false" row-key="postId" :scroll="{ x: 900 }">
      <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="partiallySelected" @change="toggleAll" /></template>
      <template #bodyCell="{ column, record }">
        <a-checkbox v-if="column.key === 'selection'" :checked="ids.includes(record.postId)" @change="(event) => toggleRow(record, event.target.checked)" />
        <dict-tag v-else-if="column.key === 'status'" :options="sys_normal_disable" :value="record.status" />
        <span v-else-if="column.key === 'createTime'">{{ parseTime(record.createTime) }}</span>
        <a-space v-else-if="column.key === 'actions'">
          <a-button type="link" @click="handleUpdate(record)" v-hasPermi="['system:post:edit']"><EditOutlined />修改</a-button>
          <a-button type="link" danger @click="handleDelete(record)" v-hasPermi="['system:post:remove']"><DeleteOutlined />删除</a-button>
        </a-space>
      </template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" :page-size-options="['10', '20', '30', '50']" :show-total="count => `共 ${count} 条`" show-size-changer show-quick-jumper responsive @change="getList" />

    <ResourceDrawer :open="open" :title="title" :width="500" destroy-on-close @close="cancel">
      <a-form ref="postRef" :model="form" :rules="rules" layout="vertical">
        <a-form-item label="岗位名称" name="postName"><a-input v-model:value="form.postName" placeholder="请输入岗位名称" /></a-form-item>
        <a-form-item label="岗位编码" name="postCode"><a-input v-model:value="form.postCode" placeholder="请输入编码名称" /></a-form-item>
        <a-form-item label="岗位顺序" name="postSort"><a-input-number v-model:value="form.postSort" :min="0" /></a-form-item>
        <a-form-item label="岗位状态" name="status"><a-radio-group v-model:value="form.status" :options="normalDisableOptions" /></a-form-item>
        <a-form-item label="备注" name="remark"><a-textarea v-model:value="form.remark" placeholder="请输入内容" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="cancel">取 消</a-button><a-button type="primary" @click="submitForm">确 定</a-button></template>
    </ResourceDrawer>
  </WorkspacePage>
</template>

<script setup name="Post">
import { DeleteOutlined, DownloadOutlined, EditOutlined, FilterOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import { listPost, addPost, delPost, getPost, updatePost } from "@/api/system/post";

const { proxy } = getCurrentInstance();
const { sys_normal_disable } = proxy.useDict("sys_normal_disable");

const postList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const total = ref(0);
const title = ref("");
const queryRef = ref();
const postRef = ref();
const normalDisableOptions = computed(() => sys_normal_disable.value.map(item => ({ label: item.label, value: item.value })));
const columns = [
  { key: 'selection', width: 55, align: 'center' },
  { title: '岗位编号', dataIndex: 'postId', key: 'postId', align: 'center' },
  { title: '岗位编码', dataIndex: 'postCode', key: 'postCode', align: 'center' },
  { title: '岗位名称', dataIndex: 'postName', key: 'postName', align: 'center' },
  { title: '岗位排序', dataIndex: 'postSort', key: 'postSort', align: 'center' },
  { title: '状态', key: 'status', align: 'center' },
  { title: '创建时间', key: 'createTime', width: 180, align: 'center' },
  { title: '操作', key: 'actions', width: 180, align: 'center' },
];
const allSelected = computed(() => postList.value.length > 0 && ids.value.length === postList.value.length);
const partiallySelected = computed(() => ids.value.length > 0 && !allSelected.value);

const data = reactive({
  form: {},
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    postCode: undefined,
    postName: undefined,
    status: undefined
  },
  rules: {
    postName: [{ required: true, message: "岗位名称不能为空", trigger: "blur" }],
    postCode: [{ required: true, message: "岗位编码不能为空", trigger: "blur" }],
    postSort: [{ required: true, message: "岗位顺序不能为空", trigger: "blur" }],
  }
});

const { queryParams, form, rules } = toRefs(data);

/** 查询岗位列表 */
function getList() {
  loading.value = true;
  listPost(queryParams.value).then(response => {
    postList.value = response.rows;
    total.value = response.total;
    handleSelectionChange([]);
    loading.value = false;
  });
}
/** 取消按钮 */
function cancel() {
  open.value = false;
  reset();
}
/** 表单重置 */
function reset() {
  form.value = {
    postId: undefined,
    postCode: undefined,
    postName: undefined,
    postSort: 0,
    status: "0",
    remark: undefined
  };
  nextTick(() => postRef.value?.clearValidate());
}
/** 搜索按钮操作 */
function handleQuery() {
  queryParams.value.pageNum = 1;
  getList();
}
/** 重置按钮操作 */
function resetQuery() {
  queryRef.value?.resetFields();
  handleQuery();
}
/** 多选框选中数据 */
function handleSelectionChange(selection) {
  ids.value = selection.map(item => item.postId);
  single.value = selection.length != 1;
  multiple.value = !selection.length;
}
function toggleRow(row, checked) {
  const selected = checked ? [...postList.value.filter(item => ids.value.includes(item.postId)), row] : postList.value.filter(item => ids.value.includes(item.postId) && item.postId !== row.postId);
  handleSelectionChange([...new Map(selected.map(item => [item.postId, item])).values()]);
}
function toggleAll(event) { handleSelectionChange(event.target.checked ? postList.value : []); }
/** 新增按钮操作 */
function handleAdd() {
  reset();
  open.value = true;
  title.value = "添加岗位";
}
/** 修改按钮操作 */
function handleUpdate(row) {
  reset();
  const postId = row.postId || ids.value;
  getPost(postId).then(response => {
    form.value = response.data;
    open.value = true;
    title.value = "修改岗位";
  });
}
/** 提交按钮 */
async function submitForm() {
  const valid = await postRef.value.validateFields().then(() => true).catch(() => false);
  if (valid) {
      if (form.value.postId != undefined) {
        updatePost(form.value).then(() => {
          feedback.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        addPost(form.value).then(() => {
          feedback.msgSuccess("新增成功");
          open.value = false;
          getList();
        });
      }
  }
}
/** 删除按钮操作 */
function handleDelete(row) {
  const postIds = row.postId || ids.value;
  feedback.confirm('是否确认删除岗位编号为"' + postIds + '"的数据项？').then(function() {
    return delPost(postIds);
  }).then(() => {
    getList();
    feedback.msgSuccess("删除成功");
  }).catch(() => {});
}
/** 导出按钮操作 */
function handleExport() {
  proxy.download("system/post/export", {
    ...queryParams.value
  }, `post_${new Date().getTime()}.xlsx`);
}

getList();
</script>

<style scoped>
.query-form { margin-bottom: 16px; gap: 8px 0; }
.query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 200px; }
:deep(.ant-pagination) { justify-content: flex-end; margin-top: 16px; }
@media (max-width: 767px) { .query-form { display: grid; } .query-form :deep(.ant-form-item), .query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 100%; } :deep(.ant-pagination-options), :deep(.ant-pagination-total-text) { display: none; } }
</style>
