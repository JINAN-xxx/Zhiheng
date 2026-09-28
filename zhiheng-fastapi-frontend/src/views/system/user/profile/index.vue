<template>
   <WorkspacePage title="个人中心" subtitle="管理个人资料、头像与登录密码">
      <a-row :gutter="20">
         <a-col :xs="24" :lg="6">
            <a-card title="个人信息" class="box-card">
               <div>
                  <div class="text-center">
                     <userAvatar />
                  </div>
                  <ul class="list-group list-group-striped">
                     <li class="list-group-item">
                        <UserOutlined />用户名称
                        <div class="pull-right">{{ state.user.userName }}</div>
                     </li>
                     <li class="list-group-item">
                        <PhoneOutlined />手机号码
                        <div class="pull-right">{{ state.user.phonenumber }}</div>
                     </li>
                     <li class="list-group-item">
                        <MailOutlined />用户邮箱
                        <div class="pull-right">{{ state.user.email }}</div>
                     </li>
                     <li class="list-group-item">
                        <ApartmentOutlined />所属部门
                        <div class="pull-right" v-if="state.user.dept">{{ state.user.dept.deptName }} / {{ state.postGroup }}</div>
                     </li>
                     <li class="list-group-item">
                        <TeamOutlined />所属角色
                        <div class="pull-right">{{ state.roleGroup }}</div>
                     </li>
                     <li class="list-group-item">
                        <CalendarOutlined />创建日期
                        <div class="pull-right">{{ state.user.createTime }}</div>
                     </li>
                  </ul>
               </div>
            </a-card>
         </a-col>
         <a-col :xs="24" :lg="18">
            <a-card title="基本资料">
               <a-tabs v-model:active-key="selectedTab">
                  <a-tab-pane key="userinfo" tab="基本资料">
                     <userInfo :user="state.user" />
                  </a-tab-pane>
                  <a-tab-pane key="resetPwd" tab="修改密码">
                     <resetPwd />
                  </a-tab-pane>
               </a-tabs>
            </a-card>
         </a-col>
      </a-row>
   </WorkspacePage>
</template>

<script setup name="Profile">
import { ApartmentOutlined, CalendarOutlined, MailOutlined, PhoneOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import userAvatar from "./userAvatar";
import userInfo from "./userInfo";
import resetPwd from "./resetPwd";
import { getUserProfile } from "@/api/system/user";

const route = useRoute()
const selectedTab = ref("userinfo")
const state = reactive({
  user: {},
  roleGroup: {},
  postGroup: {}
});

function getUser() {
  getUserProfile().then(response => {
    state.user = response.data;
    state.roleGroup = response.roleGroup;
    state.postGroup = response.postGroup;
  });
};

onMounted(() => {
  const activeTab = route.params && route.params.activeTab
  if (activeTab) {
    selectedTab.value = activeTab
  }
  getUser()
})
</script>
