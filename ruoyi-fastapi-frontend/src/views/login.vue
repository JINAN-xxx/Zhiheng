<template>
  <div class="login">
    <section class="auth-intro">
      <div class="auth-brand"><span class="auth-brand__mark">智</span><strong>{{ title }}</strong></div>
      <div class="auth-intro__copy">
        <span>AI WORKSPACE</span>
        <h1>模型能力，从这里进入工作流</h1>
        <p>统一模型接入、对话协作与开发者访问。</p>
      </div>
    </section>
    <section class="auth-panel">
    <el-form ref="loginRef" :model="loginForm" :rules="loginRules" class="login-form">
      <div class="auth-form-heading">
        <span>欢迎回来</span>
        <h2>登录 {{ title }}</h2>
      </div>
      <el-form-item prop="username">
        <el-input
          v-model="loginForm.username"
          type="text"
          size="large"
          auto-complete="off"
          placeholder="账号"
        >
          <template #prefix><svg-icon icon-class="user" class="el-input__icon input-icon" /></template>
        </el-input>
      </el-form-item>
      <el-form-item prop="password">
        <el-input
          v-model="loginForm.password"
          type="password"
          size="large"
          auto-complete="off"
          placeholder="密码"
          @keyup.enter="handleLogin"
        >
          <template #prefix><svg-icon icon-class="password" class="el-input__icon input-icon" /></template>
        </el-input>
      </el-form-item>
      <el-form-item prop="code" v-if="captchaEnabled">
        <el-input
          v-model="loginForm.code"
          size="large"
          auto-complete="off"
          placeholder="验证码"
          style="width: 63%"
          @keyup.enter="handleLogin"
        >
          <template #prefix><svg-icon icon-class="validCode" class="el-input__icon input-icon" /></template>
        </el-input>
        <div class="login-code">
          <img :src="codeUrl" @click="getCode" class="login-code-img"/>
        </div>
      </el-form-item>
      <el-checkbox v-model="loginForm.rememberMe" style="margin:0px 0px 25px 0px;">记住密码</el-checkbox>
      <el-form-item style="width:100%;">
        <el-button
          :loading="loading"
          size="large"
          type="primary"
          style="width:100%;"
          @click.prevent="handleLogin"
        >
          <span v-if="!loading">登 录</span>
          <span v-else>登 录 中...</span>
        </el-button>
        <div style="float: right;" v-if="register">
          <router-link class="link-type" :to="'/register'">立即注册</router-link>
        </div>
      </el-form-item>
    </el-form>
    <!--  底部  -->
    <div class="el-login-footer">
      <span>{{ footerContent }}</span>
    </div>
    </section>
  </div>
</template>

<script setup>
import { getCodeImg } from "@/api/login";
import Cookies from "js-cookie";
import { encrypt, decrypt } from "@/utils/jsencrypt";
import useUserStore from '@/store/modules/user'
import defaultSettings from '@/settings'

const title = import.meta.env.VITE_APP_TITLE;
const footerContent = defaultSettings.footerContent
const userStore = useUserStore();
const route = useRoute();
const router = useRouter();
const { proxy } = getCurrentInstance();

const loginForm = ref({
  username: "",
  password: "",
  rememberMe: false,
  code: "",
  uuid: ""
});

const loginRules = {
  username: [{ required: true, trigger: "blur", message: "请输入您的账号" }],
  password: [{ required: true, trigger: "blur", message: "请输入您的密码" }],
  code: [{ required: true, trigger: "change", message: "请输入验证码" }]
};

const codeUrl = ref("");
const loading = ref(false);
// 验证码开关
const captchaEnabled = ref(true);
// 注册开关
const register = ref(false);
const redirect = ref(undefined);

watch(route, (newRoute) => {
    redirect.value = newRoute.query && newRoute.query.redirect;
}, { immediate: true });

function handleLogin() {
  proxy.$refs.loginRef.validate(valid => {
    if (valid) {
      loading.value = true;
      // 勾选了需要记住密码设置在 cookie 中设置记住用户名和密码
      if (loginForm.value.rememberMe) {
        Cookies.set("username", loginForm.value.username, { expires: 30 });
        Cookies.set("password", encrypt(loginForm.value.password), { expires: 30 });
        Cookies.set("rememberMe", loginForm.value.rememberMe, { expires: 30 });
      } else {
        // 否则移除
        Cookies.remove("username");
        Cookies.remove("password");
        Cookies.remove("rememberMe");
      }
      // 调用action的登录方法
      userStore.login(loginForm.value).then(() => {
        const query = route.query;
        const otherQueryParams = Object.keys(query).reduce((acc, cur) => {
          if (cur !== "redirect") {
            acc[cur] = query[cur];
          }
          return acc;
        }, {});
        router.push({ path: redirect.value || "/", query: otherQueryParams });
      }).catch(() => {
        loading.value = false;
        // 重新获取验证码
        if (captchaEnabled.value) {
          getCode();
        }
      });
    }
  });
}

function getCode() {
  getCodeImg().then(res => {
    captchaEnabled.value = res.captchaEnabled === undefined ? true : res.captchaEnabled;
    register.value = res.registerEnabled === undefined ? false : res.registerEnabled;
    if (captchaEnabled.value) {
      codeUrl.value = "data:image/gif;base64," + res.img;
      loginForm.value.uuid = res.uuid;
    }
  });
}

function getCookie() {
  const username = Cookies.get("username");
  const password = Cookies.get("password");
  const rememberMe = Cookies.get("rememberMe");
  loginForm.value = {
    username: username === undefined ? loginForm.value.username : username,
    password: password === undefined ? loginForm.value.password : decrypt(password),
    rememberMe: rememberMe === undefined ? false : Boolean(rememberMe)
  };
}

getCode();
getCookie();
</script>

<style lang="scss" scoped>
.login {
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
  margin-bottom: 28px;
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

.login-form {
  z-index: 1;
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

.login-code {
  width: 33%;
  height: 42px;
  float: right;

  img {
    cursor: pointer;
    vertical-align: middle;
  }
}

.login-code-img {
  height: 42px;
  padding-left: 12px;
}

.el-login-footer {
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
