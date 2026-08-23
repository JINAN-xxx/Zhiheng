<template>
   <a-form ref="userRef" :model="form" :rules="rules" layout="vertical">
      <a-form-item label="用户昵称" name="nickName">
         <a-input v-model:value="form.nickName" :maxlength="30" />
      </a-form-item>
      <a-form-item label="手机号码" name="phonenumber">
         <a-input v-model:value="form.phonenumber" :maxlength="11" />
      </a-form-item>
      <a-form-item label="邮箱" name="email">
         <a-input v-model:value="form.email" :maxlength="50" />
      </a-form-item>
      <a-form-item label="性别">
         <a-radio-group v-model:value="form.sex">
            <a-radio value="0">男</a-radio>
            <a-radio value="1">女</a-radio>
         </a-radio-group>
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
import { updateUserProfile } from "@/api/system/user";

const props = defineProps({
  user: {
    type: Object
  }
});

const { proxy } = getCurrentInstance();

const userRef = ref();
const form = ref({});
const rules = ref({
  nickName: [{ required: true, message: "用户昵称不能为空", trigger: "blur" }],
  email: [{ required: true, message: "邮箱地址不能为空", trigger: "blur" }, { type: "email", message: "请输入正确的邮箱地址", trigger: ["blur", "change"] }],
  phonenumber: [{ required: true, message: "手机号码不能为空", trigger: "blur" }, { pattern: /^1[3|4|5|6|7|8|9][0-9]\d{8}$/, message: "请输入正确的手机号码", trigger: "blur" }],
});

/** 提交按钮 */
function submit() {
  userRef.value.validateFields().then(() => {
    updateUserProfile(form.value).then(() => {
      feedback.msgSuccess("修改成功");
      props.user.phonenumber = form.value.phonenumber;
      props.user.email = form.value.email;
    });
  }).catch(() => {});
};

/** 关闭按钮 */
function close() {
  proxy.$tab.closePage();
};

// 回显当前登录用户信息
watch(() => props.user, user => {
  if (user) {
    form.value = { nickName: user.nickName, phonenumber: user.phonenumber, email: user.email, sex: user.sex };
  }
},{ immediate: true });
</script>
