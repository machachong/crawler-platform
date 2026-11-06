<template>
  <router-view v-if="route.meta.public" />
  <a-layout v-else-if="auth.user" style="min-height: 100vh">
    <a-layout-sider v-model:collapsed="collapsed" collapsible>
      <div class="brand">抓取台</div>
      <a-menu v-model:selectedKeys="selectedKeys" theme="dark" mode="inline" @click="onMenu">
        <a-menu-item key="1">
          <dashboard-outlined />
          <span>任务中心</span>
        </a-menu-item>
        <a-menu-item v-if="canWrite()" key="2">
          <plus-circle-outlined />
          <span>新建抓取</span>
        </a-menu-item>
        <a-menu-item v-if="auth.user.role === 'admin'" key="3">
          <team-outlined />
          <span>用户管理</span>
        </a-menu-item>
        <a-menu-item key="4">
          <snippets-outlined />
          <span>采集模板</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>
    <a-layout>
      <a-layout-header class="header">
        <h2>自助爬虫平台</h2>
        <a-space class="account" wrap>
          <span>{{ auth.user.username }} · {{ roleNames[auth.user.role] }}</span>
          <a-button @click="passwordOpen = true">修改密码</a-button>
          <a-button :loading="loggingOut" @click="signOut">退出登录</a-button>
        </a-space>
      </a-layout-header>
      <a-layout-content class="content">
        <router-view />
      </a-layout-content>
      <a-layout-footer style="text-align: center">
        仅抓取你有权访问的页面，默认遵守 robots.txt
      </a-layout-footer>
    </a-layout>
  </a-layout>
  <a-modal v-model:open="passwordOpen" title="修改密码" :footer="null" :mask-closable="false" @cancel="resetPasswordForm">
    <a-form :model="passwordForm" layout="vertical" @finish="changePassword">
      <a-form-item label="当前密码" name="current_password" :rules="[{ required: true, message: '请输入当前密码' }]">
        <a-input-password v-model:value="passwordForm.current_password" autocomplete="current-password" :maxlength="128" />
      </a-form-item>
      <a-form-item label="新密码" name="new_password" :rules="[{ required: true, min: 12, max: 128, message: '请输入 12–128 位新密码' }]">
        <a-input-password v-model:value="passwordForm.new_password" autocomplete="new-password" :maxlength="128" />
      </a-form-item>
      <a-form-item label="确认新密码" name="confirm" :rules="[{ validator: confirmPassword }]">
        <a-input-password v-model:value="passwordForm.confirm" autocomplete="new-password" :maxlength="128" />
      </a-form-item>
      <p>修改后需要重新登录。</p>
      <a-button type="primary" html-type="submit" :loading="changingPassword">保存密码</a-button>
    </a-form>
  </a-modal>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { DashboardOutlined, PlusCircleOutlined, TeamOutlined, SnippetsOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import api, { errorMessage } from './utils/api';
import { auth, canWrite, clearAuth, logout, roleNames } from './utils/auth';

const route = useRoute();
const router = useRouter();
const collapsed = ref(false);
const selectedKeys = ref(['1']);
const passwordOpen = ref(false), changingPassword = ref(false), loggingOut = ref(false);
const passwordForm = reactive({ current_password: '', new_password: '', confirm: '' });

function resetPasswordForm() {
  Object.assign(passwordForm, { current_password: '', new_password: '', confirm: '' });
}
async function confirmPassword() {
  if (!passwordForm.confirm || passwordForm.confirm !== passwordForm.new_password) throw new Error('两次输入的密码不一致');
}
async function changePassword() {
  changingPassword.value = true;
  try {
    await api.post('/auth/password', { current_password: passwordForm.current_password, new_password: passwordForm.new_password });
    passwordOpen.value = false; resetPasswordForm(); clearAuth();
    message.success('密码已修改，请重新登录'); await router.replace('/login');
  } catch (error) { message.error(errorMessage(error)); }
  finally { changingPassword.value = false; }
}
async function signOut() {
  loggingOut.value = true;
  try { await logout(); await router.replace('/login'); }
  catch (error) { message.error(errorMessage(error)); }
  finally { loggingOut.value = false; }
}
function expired() {
  clearAuth(); passwordOpen.value = false; resetPasswordForm();
  if (route.path !== '/login') router.replace({ path: '/login', query: { redirect: route.fullPath } });
}
onMounted(() => window.addEventListener('auth-expired', expired));
onUnmounted(() => window.removeEventListener('auth-expired', expired));

watch(
  () => route.path,
  (path) => {
    selectedKeys.value = path.startsWith('/templates') ? ['4'] : path.startsWith('/users') ? ['3'] : path.startsWith('/create') ? ['2'] : ['1'];
  },
  { immediate: true },
);

function onMenu({ key }) {
  router.push(key === '4' ? '/templates' : key === '3' ? '/users' : key === '2' ? '/create' : '/');
}
</script>

<style scoped>
.brand {
  height: 48px;
  margin: 12px 16px;
  color: #fff;
  font-weight: 700;
  letter-spacing: 0.08em;
  display: flex;
  align-items: center;
}
.header {
  background: #fff;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.header h2 {
  margin: 0;
}
.header span {
  color: #667;
}
.header { height: auto; min-height: 64px; flex-wrap: wrap; padding-top: 12px; padding-bottom: 12px; }
.content {
  margin: 16px;
  min-height: 280px;
}
</style>
