<template>
  <a-config-provider :theme="theme">
  <router-view v-if="route.meta.public" />
  <a-layout v-else-if="auth.user" class="workspace">
    <a-layout-sider v-model:collapsed="collapsed" class="workspace-nav" :width="220" :collapsed-width="64" breakpoint="md" collapsible>
      <router-link to="/" class="brand" aria-label="抓取台首页">
        <span class="brand-mark"><scan-outlined /></span>
        <span v-if="!collapsed">抓取台<small>采集工作台</small></span>
      </router-link>
      <div v-if="!collapsed" class="nav-label">工作空间</div>
      <a-menu v-model:selectedKeys="selectedKeys" theme="dark" mode="inline" class="workspace-menu" @click="onMenu">
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
    <a-layout class="workspace-main">
      <a-layout-header class="header">
        <div class="location"><span>工作台</span><strong>{{ pageName }}</strong></div>
        <div class="account">
          <span class="avatar" aria-hidden="true">{{ auth.user.username[0].toUpperCase() }}</span>
          <div class="account-info"><strong>{{ auth.user.username }}</strong><small>{{ roleNames[auth.user.role] }}</small></div>
          <a-button size="small" @click="passwordOpen = true">改密码</a-button>
          <a-button size="small" :loading="loggingOut" @click="signOut">退出</a-button>
        </div>
      </a-layout-header>
      <a-layout-content class="content">
        <router-view />
      </a-layout-content>
      <a-layout-footer class="workspace-footer">
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
  </a-config-provider>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { DashboardOutlined, PlusCircleOutlined, ScanOutlined, TeamOutlined, SnippetsOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import api, { errorMessage } from './utils/api';
import { auth, canWrite, clearAuth, logout, roleNames } from './utils/auth';

const route = useRoute();
const router = useRouter();
const collapsed = ref(false);
const selectedKeys = ref(['1']);
const theme = { token: { colorPrimary: '#4e55d9', colorBgLayout: '#f2f5fb', borderRadius: 10 } };
const pageName = computed(() => route.path.startsWith('/results/')
  ? '采集结果'
  : ({ '1': '任务中心', '2': '新建抓取', '3': '用户管理', '4': '采集模板' })[selectedKeys.value[0]]);
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
  if (!route.meta.public) router.replace({ path: '/login', query: { redirect: route.fullPath } });
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
.workspace { min-height: 100vh; }
.workspace-main { min-width: 0; }
.workspace-nav { position: sticky; top: 0; height: 100dvh; background: #151d38; }
.workspace-nav :deep(.ant-layout-sider-trigger) { background: #202946; }
.brand { height: 86px; display: flex; gap: 12px; align-items: center; padding: 0 19px; color: #fff; font-size: 18px; font-weight: 720; white-space: nowrap; }
.brand small { display: block; margin-top: 3px; color: #b1bedb; font-size: 11px; font-weight: 450; letter-spacing: .09em; }
.brand-mark { display: grid; place-items: center; width: 36px; height: 36px; flex-shrink: 0; border-radius: 11px; background: #5c65ed; }
.workspace-nav.ant-layout-sider-collapsed .brand { padding: 0 14px; }
.nav-label { padding: 8px 20px 10px; color: #9eabce; font-size: 11px; letter-spacing: .12em; }
.workspace-menu { background: transparent; border: 0; padding: 0 6px; }
.workspace-menu :deep(.ant-menu-item) { margin-block: 6px; height: 46px; }
.workspace-menu :deep(.ant-menu-item-selected) { background: #4e55d9; }
.workspace-menu.ant-menu-inline-collapsed { padding: 0; }
.header { min-height: 68px; height: auto; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 18px; padding: 10px clamp(12px, 3vw, 32px); line-height: normal; background: #fff; border-bottom: 1px solid #e4eaf5; }
.location { display: flex; align-items: center; gap: 9px; color: #66748c; font-size: 13px; }
.location strong { color: #233050; }
.location strong::before { content: '/'; margin-right: 9px; color: #acb7ca; }
.account { display: flex; align-items: center; gap: 8px; max-width: 100%; }
.avatar { width: 34px; height: 34px; flex-shrink: 0; display: grid; place-items: center; border-radius: 50%; background: #e9edff; color: #4249b8; font-weight: 700; }
.account-info { min-width: 0; margin-right: 6px; max-width: 140px; }
.account-info strong, .account-info small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-info small { margin-top: 3px; color: #68758d; }
.content { width: min(100%, 1640px); margin-inline: auto; padding: clamp(12px, 2.5vw, 32px); min-height: 280px; }
.workspace-footer { text-align: center; color: #66748c; font-size: 12px; }
@media (max-width: 600px) {
  .account-info { margin-right: 0; }
  .avatar { width: 26px; height: 26px; }
  .header { min-height: 56px; }
  .location > span, .location strong::before { display: none; }
}
</style>
