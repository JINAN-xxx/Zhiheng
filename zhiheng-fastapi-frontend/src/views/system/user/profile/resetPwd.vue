<template>
  <a-form ref="pwdRef" :model="user" :rules="rules" layout="vertical">
    <a-form-item label="旧密码" name="oldPassword">
      <a-input-password v-model:value="user.oldPassword" placeholder="请输入旧密码" autocomplete="current-password" />
    </a-form-item>
    <a-form-item label="新密码" name="newPassword" :rules="infoPwdValidator">
      <a-input-password v-model:value="user.newPassword" placeholder="请输入新密码" autocomplete="new-password" />
    </a-form-item>
    <a-form-item label="确认密码" name="confirmPassword">
      <a-input-password v-model:value="user.confirmPassword" placeholder="请确认新密码" autocomplete="new-password" />
    </a-form-item>
    <a-form-item>
      <a-space>
        <a-button type="primary" @click="submit"><SaveOutlined />保存</a-button>
        <a-button danger @click="close"><CloseOutlined />关闭</a-button>
      </a-space>
    </a-form-item>
  </a-form>
</template>

<script setup>
import { CloseOutlined, SaveOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import { usePasswordRule } from "@/utils/passwordRule";
import { updateUserPwd } from "@/api/system/user";

const { proxy } = getCurrentInstance();
const { infoPwdValidator } = usePasswordRule();
const pwdRef = ref();

const user = reactive({
  oldPassword: undefined,
  newPassword: undefined,
  confirmPassword: undefined
});

const equalToPassword = (rule, value, callback) => {
  if (user.newPassword !== value) {
    callback(new Error("两次输入的密码不一致"));
  } else {
    callback();
  }
};
const rules = ref({
  oldPassword: [{ required: true, message: "旧密码不能为空", trigger: "blur" }],
  confirmPassword: [{ required: true, message: "确认密码不能为空", trigger: "blur" }, { required: true, validator: equalToPassword, trigger: "blur" }]
});

/** 提交按钮 */
function submit() {
  pwdRef.value.validateFields().then(() => {
    updateUserPwd(user.oldPassword, user.newPassword).then(() => {
      feedback.msgSuccess("修改成功");
    });
  }).catch(() => {});
};
/** 关闭按钮 */
function close() {
  proxy.$tab.closePage();
};
</script>
