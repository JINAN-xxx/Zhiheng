<template>
  <div class="register">
    <section class="auth-intro">
      <div class="auth-brand"><span class="auth-brand__mark">智</span><strong>{{ title }}</strong></div>
      <div class="auth-intro__copy">
        <span>AI WORKSPACE</span>
        <h1>创建你的智衡工作台账户</h1>
        <p>连接模型、管理访问并开始智能协作。</p>
      </div>
    </section>
    <section class="auth-panel">
    <el-form ref="registerRef" :model="registerForm" :rules="registerRules" class="register-form">
      <div class="auth-form-heading">
        <span>创建账户</span>
        <h2>注册 {{ title }}</h2>
      </div>
      <el-form-item prop="username">
        <el-input 
          v-model="registerForm.username" 
          type="text" 
          size="large" 
          auto-complete="off" 
          placeholder="账号"
        >
          <template #prefix><svg-icon icon-class="user" class="el-input__icon input-icon" /></template>
        </el-input>
      </el-form-item>
      <el-form-item prop="password" :rules="registerPwdValidator">
        <el-input
          v-model="registerForm.password"
          type="password"
          size="large" 
          auto-complete="off"
          placeholder="密码"
          @keyup.enter="handleRegister"
        >
          <template #prefix><svg-icon icon-class="password" class="el-input__icon input-icon" /></template>
        </el-input>
      </el-form-item>
      <el-form-item prop="confirmPassword">
        <el-input
          v-model="registerForm.confirmPassword"
          type="password"
          size="large" 
          auto-complete="off"
          placeholder="确认密码"
          @keyup.enter="handleRegister"
        >
          <template #prefix><svg-icon icon-class="password" class="el-input__icon input-icon" /></template>
        </el-input>
      </el-form-item>
      <el-form-item prop="code" v-if="captchaEnabled">
        <el-input
          size="large" 
          v-model="registerForm.code"
          auto-complete="off"
          placeholder="验证码"
          style="width: 63%"
          @keyup.enter="handleRegister"
        >
          <template #prefix><svg-icon icon-class="validCode" class="el-input__icon input-icon" /></template>
        </el-input>
        <div class="register-code">
          <img :src="codeUrl" @click="getCode" class="register-code-img"/>
        </div>
      </el-form-item>
      <el-form-item style="width:100%;">
        <el-button
          :loading="loading"
          size="large" 
          type="primary"
          style="width:100%;"
          @click.prevent="handleRegister"
        >
          <span v-if="!loading">注 册</span>
          <span v-else>注 册 中...</span>
        </el-button>
        <div style="float: right;">
          <router-link class="link-type" :to="'/login'">使用已有账户登录</router-link>
        </div>
      </el-form-item>
    </el-form>
    <!--  底部  -->
    <div class="el-register-footer">
      <span>{{ footerContent }}</span>
    </div>
    </section>
  </div>
</template>

<script setup>
import { ElMessageBox } from "element-plus";
import { getCodeImg, register } from "@/api/login";
import defaultSettings from '@/settings'
import { usePasswordRule } from "@/utils/passwordRule"

const title = import.meta.env.VITE_APP_TITLE;
const footerContent = defaultSettings.footerContent
const router = useRouter();
const { proxy } = getCurrentInstance();
const { registerPwdValidator } = usePasswordRule()

const registerForm = ref({
  username: "",
  password: "",
  confirmPassword: "",
  code: "",
  uuid: ""
});

const equalToPassword = (rule, value, callback) => {
  if (registerForm.value.password !== value) {
    callback(new Error("两次输入的密码不一致"));
  } else {
    callback();
  }
};

const registerRules = {
  username: [
    { required: true, trigger: "blur", message: "请输入您的账号" },
    { min: 2, max: 20, message: "用户账号长度必须介于 2 和 20 之间", trigger: "blur" }
  ],
  confirmPassword: [
    { required: true, trigger: "blur", message: "请再次输入您的密码" },
    { required: true, validator: equalToPassword, trigger: "blur" }
  ],
  code: [{ required: true, trigger: "change", message: "请输入验证码" }]
};

const codeUrl = ref("");
const loading = ref(false);
const captchaEnabled = ref(true);

function handleRegister() {
  proxy.$refs.registerRef.validate(valid => {
    if (valid) {
      loading.value = true;
      register(registerForm.value).then(res => {
        const username = registerForm.value.username;
        ElMessageBox.alert("<font color='red'>恭喜你，您的账号 " + username + " 注册成功！</font>", "系统提示", {
          dangerouslyUseHTMLString: true,
          type: "success",
        }).then(() => {
          router.push("/login");
        }).catch(() => {});
      }).catch(() => {
        loading.value = false;
        if (captchaEnabled) {
          getCode();
        }
      });
    }
  });
}

function getCode() {
  getCodeImg().then(res => {
    captchaEnabled.value = res.captchaEnabled === undefined ? true : res.captchaEnabled;
    if (captchaEnabled.value) {
      codeUrl.value = "data:image/gif;base64," + res.img;
      registerForm.value.uuid = res.uuid;
    }
  });
}

getCode();
</script>

<style lang="scss" scoped>
.register {
  display: flex;
  min-height: 100%;
  background: #f3f6f5 url("../assets/images/login-background.jpg") center / cover no-repeat;
  font-family: var(--workspace-font-family);
}

.auth-intro {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  justify-content: space-between;
  padding: 48px clamp(40px, 7vw, 104px) 72px;
  color: #fff;
  background: linear-gradient(90deg, rgba(14, 49, 54, 0.84), rgba(25, 91, 103, 0.42));
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 17px;
}

.auth-brand__mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: var(--workspace-radius-sm);
  color: var(--workspace-main-deep);
  background: #fff;
  font-weight: 700;
}

.auth-intro__copy {
  max-width: 560px;
}

.auth-intro__copy span {
  font-size: 12px;
  letter-spacing: 0.12em;
  opacity: 0.76;
}

.auth-intro__copy h1 {
  margin: 14px 0 12px;
  font-size: 48px;
  font-weight: 650;
  line-height: 1.2;
}

.auth-intro__copy p {
  margin: 0;
  font-size: 16px;
  opacity: 0.82;
}

.auth-panel {
  position: relative;
  display: grid;
  width: min(480px, 42vw);
  min-width: 400px;
  place-items: center;
  padding: 48px;
  background: rgba(255, 255, 255, 0.97);
}

.auth-form-heading {
  margin-bottom: 26px;
}

.auth-form-heading span {
  color: var(--workspace-main-deep);
  font-size: 13px;
  font-weight: 600;
}

.auth-form-heading h2 {
  margin: 7px 0 0;
  color: var(--workspace-text);
  font-size: 25px;
  letter-spacing: 0;
}

.register-form {
  width: min(100%, 360px);

  .el-input,
  .el-input input {
    height: 42px;
  }

  .input-icon {
    width: 14px;
    height: 39px;
    margin-left: 0;
  }
}

.register-code {
  width: 33%;
  height: 42px;
  float: right;

  img {
    cursor: pointer;
    vertical-align: middle;
  }
}

.register-code-img {
  height: 42px;
  padding-left: 12px;
}

.el-register-footer {
  position: absolute;
  right: 0;
  bottom: 20px;
  left: 0;
  color: var(--workspace-text-muted);
  font-size: 12px;
  text-align: center;
}

html.dark .auth-panel {
  background: rgba(24, 27, 27, 0.97);
}

@media (max-width: 760px) {
  .auth-intro {
    display: none;
  }

  .auth-panel {
    width: 100%;
    min-width: 0;
    padding: 28px;
  }
}
</style>
