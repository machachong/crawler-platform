<template>
  <main class="login-page">
    <a-card class="login-card">
      <h1>登录抓取台</h1>
      <p class="intro">使用你的账号访问采集任务与结构化数据。</p>
      <a-alert v-if="error" type="error" :message="error" show-icon style="margin-bottom: 20px" />
      <a-form layout="vertical" :model="form" @finish="submit">
        <a-form-item label="账号" name="username" :rules="[{ required: true, message: '请输入账号' }]">
          <a-input v-model:value="form.username" autocomplete="username" :maxlength="64" placeholder="请输入账号" />
        </a-form-item>
        <a-form-item label="密码" name="password" :rules="[{ required: true, message: '请输入密码' }]">
          <a-input-password v-model:value="form.password" autocomplete="current-password" :maxlength="128" placeholder="请输入密码" />
        </a-form-item>
        <a-button block type="primary" html-type="submit" :loading="loading">登录</a-button>
      </a-form>
      <p class="help">没有账号或忘记密码？请联系平台管理员。</p>
    </a-card>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { login } from '../utils/auth';
import { errorMessage } from '../utils/api';

const route = useRoute();
const router = useRouter();
const form = reactive({ username: '', password: '' });
const loading = ref(false);
const error = ref('');

async function submit() {
  loading.value = true;
  error.value = '';
  try {
    await login(form.username.trim(), form.password);
    form.password = '';
    const target = route.query.redirect;
    await router.replace(typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')
      && !target.startsWith('/login') ? target : '/');
  } catch (cause) {
    error.value = errorMessage(cause);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-page { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #f0f2f5; }
.login-card { width: 100%; max-width: 420px; }
h1 { font-size: 24px; margin: 0 0 8px; }
.intro { color: #667085; margin-bottom: 28px; }
.help { margin: 24px 0 0; color: #667085; font-size: 13px; }
</style>
