<template>
  <ResourceDrawer
    :title="`转移文件 - ${fileName}`"
    :open="visible"
    :width="620"
    @close="visible = false"
  >
    <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
      <a-form-item label="新所有者" name="ownerUserId">
        <a-select
          v-model:value="form.ownerUserId"
          show-search
          allow-clear
          :filter-option="false"
          :loading="userLoading"
          @search="searchUsers"
          @dropdown-visible-change="open => open && searchUsers('')"
          @change="handleUserChange"
          placeholder="输入用户名称搜索"
          style="width: 100%"
          :options="userSelectOptions"
        />
      </a-form-item>
      <a-form-item label="所属部门" name="deptId">
        <a-tree-select
          v-model:value="form.deptId"
          :tree-data="deptOptions"
          :field-names="{ value: 'id', label: 'label', children: 'children' }"
          placeholder="请选择所属部门"
          show-search
          allow-clear
          tree-default-expand-all
          style="width: 100%"
        />
      </a-form-item>
      <a-form-item label="上传人权限">
        <a-switch
          v-model:checked="form.retainUploaderAccess"
          checked-children="保留"
          un-checked-children="移除"
        />
        <div class="form-tip">
          {{
            form.retainUploaderAccess
              ? "原上传人继续拥有内置下载权限，匹配的显式拒绝仍可覆盖。"
              : "原上传人不再因上传身份获得下载权限，上传记录仍会保留。"
          }}
        </div>
      </a-form-item>
      <a-form-item label="转移原因" name="reason">
        <a-textarea
          v-model:value="form.reason"
          :rows="3"
          :maxlength="500"
          show-count
          placeholder="请输入转移原因"
        />
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="dialog-footer">
        <a-button type="primary" :loading="saving" @click="submit">
          确 定
        </a-button>
        <a-button @click="visible = false">取 消</a-button>
      </div>
    </template>
  </ResourceDrawer>
</template>

<script setup>
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import feedback from '@/plugins/antdFeedback'
import {
  getFileAclDeptTree,
  searchFileAclSubjects,
  transferFile
} from "@/api/system/file";

const emit = defineEmits(["refresh"]);
const visible = ref(false);
const saving = ref(false);
const userLoading = ref(false);
const fileIds = ref("");
const fileName = ref("");
const userOptions = ref([]);
const deptOptions = ref([]);
const formRef = ref();
const form = reactive({
  ownerUserId: undefined,
  deptId: undefined,
  retainUploaderAccess: true,
  reason: undefined
});
const rules = {
  ownerUserId: [
    { required: true, message: "请选择新所有者", trigger: "change" }
  ],
  deptId: [{ required: true, message: "请选择所属部门", trigger: "change" }],
  reason: [{ required: true, message: "请输入转移原因", trigger: "blur" }]
};
const userSelectOptions = computed(() => userOptions.value.map(item => ({ label: item.subjectName, value: item.subjectId })));

function open(row, selectedIds) {
  const isSingle = row?.fileId;
  fileIds.value = isSingle ? row.fileId : selectedIds.join(",");
  fileName.value = isSingle ? row.originalName : `${selectedIds.length}个文件`;
  Object.assign(form, {
    ownerUserId: undefined,
    deptId: undefined,
    retainUploaderAccess: true,
    reason: undefined
  });
  userOptions.value = [];
  visible.value = true;
  nextTick(() => formRef.value?.clearValidate());
  getFileAclDeptTree().then(response => {
    deptOptions.value = response.data;
  });
  searchUsers("");
}

function searchUsers(keyword) {
  userLoading.value = true;
  searchFileAclSubjects({ subjectType: "user", keyword })
    .then(response => {
      userOptions.value = response.data;
    })
    .finally(() => {
      userLoading.value = false;
    });
}

function handleUserChange(userId) {
  const targetUser = userOptions.value.find(item => item.subjectId === userId);
  if (targetUser?.deptId) {
    form.deptId = targetUser.deptId;
  }
}

function submit() {
  formRef.value.validateFields().then(() => {
    saving.value = true;
    transferFile(fileIds.value, form)
      .then(() => {
        visible.value = false;
        emit("refresh");
        feedback.msgSuccess("文件转移成功");
      })
      .finally(() => {
        saving.value = false;
      });
  }).catch(() => {});
}

defineExpose({ open });
</script>

<style scoped>
.form-tip {
  width: 100%;
  margin-top: 4px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  line-height: 20px;
}
</style>
