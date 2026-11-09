<template>
  <main class="auth-page">
    <section class="auth-shell">
      <aside class="auth-story">
        <div class="auth-brand"><span><scan-outlined /></span>抓取台</div>
        <div class="story-copy">
          <p class="kicker">网页采集工作台</p>
          <h2>从网页到<br />可分享的结果</h2>
          <p>管理抓取任务、整理网页内容，并把采集结果发布成独立网页。</p>
          <ol class="story-steps">
            <li><span>01</span><div><strong>配置采集</strong><small>从单页试跑，或按计划持续抓取</small></div></li>
            <li><span>02</span><div><strong>查看结果</strong><small>核对正文、提取字段和运行记录</small></div></li>
            <li><span>03</span><div><strong>预览与发布</strong><small>把结果整理成可分享的网页</small></div></li>
          </ol>
        </div>
      </aside>

      <section class="auth-form">
        <nav class="auth-switch" aria-label="账号入口">
          <router-link :to="{ name: 'login', query: route.query }">登录</router-link>
          <router-link :to="{ name: 'register', query: route.query }">注册</router-link>
        </nav>
        <h1>{{ registering ? '创建账号' : '登录抓取台' }}</h1>
        <p class="intro">{{ registering ? '提交注册后，管理员启用账号即可登录。' : '登录后查看你的任务和采集结果。' }}</p>
        <a-alert v-if="error" type="error" :message="error" show-icon class="form-message" />
        <a-form layout="vertical" :model="form" size="large" @finish="submit">
          <a-form-item label="账号" name="username" :rules="usernameRules">
            <a-input v-model:value="form.username" autocomplete="username" :maxlength="64" placeholder="请输入账号" />
          </a-form-item>
          <a-form-item label="密码" name="password" :rules="passwordRules">
            <a-input-password v-model:value="form.password"
              :autocomplete="registering ? 'new-password' : 'current-password'" :maxlength="128"
              :placeholder="registering ? '设置 12–128 位密码' : '请输入密码'" />
          </a-form-item>
          <a-form-item v-if="registering" label="确认密码" name="confirmation"
            :rules="[{ required: true, message: '请再次输入密码' }, { validator: confirmPassword }]">
            <a-input-password v-model:value="form.confirmation" autocomplete="new-password" :maxlength="128" placeholder="再次输入密码" />
          </a-form-item>
          <a-form-item label="图片验证码" name="captcha_code"
            :rules="[{ required: true, len: 5, message: '请输入图片中的 5 位字符' }]">
            <div class="captcha-row">
              <a-input v-model:value="form.captcha_code" autocomplete="off" autocapitalize="characters"
                :spellcheck="false" :maxlength="5" placeholder="输入 5 位字符" aria-label="图片验证码" />
              <button type="button" class="captcha-image" :disabled="captchaLoading || loading"
                aria-label="换一张验证码" title="点击换一张" @click="refreshCaptcha">
                <img v-if="challenge" :src="challenge.image" alt="验证码图片" width="156" height="52" />
                <span v-else>{{ captchaLoading ? '加载中…' : '重新获取' }}</span>
                <reload-outlined class="refresh-icon" />
              </button>
            </div>
            <p class="captcha-help">{{ captchaError || '验证码有效期 3 分钟，点击图片可换一张。' }}</p>
          </a-form-item>
          <a-button block type="primary" html-type="submit" :loading="loading" :disabled="!challenge || captchaLoading">
            {{ registering ? '提交注册' : '登录' }}
          </a-button>
        </a-form>
        <p class="help">{{ registering ? '账号启用后，可在任务中心新建抓取。' : '忘记密码或账号未启用，请联系管理员。' }}</p>
      </section>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ReloadOutlined, ScanOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import api, { errorMessage } from '../utils/api';
import { login } from '../utils/auth';

const route = useRoute();
const router = useRouter();
const registering = computed(() => route.name === 'register');
const form = reactive({ username: '', password: '', confirmation: '', captcha_code: '' });
const usernameRules = [
  { required: true, message: '请输入账号' },
  { pattern: /^[a-zA-Z0-9_.-]{3,64}$/, message: '账号使用 3–64 位字母、数字或 _ . -' },
];
const passwordRules = computed(() => [
  { required: true, message: '请输入密码' },
  ...(registering.value ? [{ min: 12, message: '密码至少需要 12 位' }] : []),
]);
const loading = ref(false);
const error = ref('');
const challenge = ref(null);
const captchaError = ref('');
const captchaLoading = ref(false);
let challengeRequest = 0;

async function confirmPassword() {
  if (!form.confirmation || form.confirmation !== form.password) throw new Error('两次输入的密码不一致');
}

async function refreshCaptcha() {
  const request = ++challengeRequest;
  challenge.value = null;
  form.captcha_code = '';
  captchaError.value = '';
  captchaLoading.value = true;
  try {
    const { data } = await api.get('/auth/captcha', {
      params: { purpose: registering.value ? 'register' : 'login' },
    });
    if (request === challengeRequest) challenge.value = data;
  } catch (cause) {
    if (request === challengeRequest) captchaError.value = errorMessage(cause);
  } finally {
    if (request === challengeRequest) captchaLoading.value = false;
  }
}

async function submit() {
  if (!challenge.value || loading.value) return;
  const isRegistration = registering.value;
  const credentials = {
    username: form.username.trim(),
    password: form.password,
    captcha_id: challenge.value.captcha_id,
    captcha_code: form.captcha_code.trim(),
  };
  loading.value = true;
  error.value = '';
  try {
    if (isRegistration) {
      await api.post('/auth/register', credentials);
      await router.replace({ name: 'login', query: route.query });
      message.success('注册成功，管理员启用账号后即可登录。');
      return;
    }
    await login(credentials);
    form.password = '';
    const target = route.query.redirect;
    await router.replace(typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')
      && !target.startsWith('/login') && !target.startsWith('/register') ? target : '/');
  } catch (cause) {
    if (registering.value === isRegistration) {
      error.value = errorMessage(cause);
      await refreshCaptcha();
    }
  } finally {
    loading.value = false;
  }
}

watch(registering, () => {
  form.password = '';
  form.confirmation = '';
  error.value = '';
  refreshCaptcha();
});
onMounted(refreshCaptcha);
onUnmounted(() => { challengeRequest++; });
</script>

<style scoped>
.auth-page { min-height: 100vh; display: grid; place-items: center; padding: clamp(16px, 4vh, 48px) clamp(16px, 5vw, 64px); background: #edf1f9; }
.auth-shell { width: min(100%, 1040px); display: grid; grid-template-columns: 1fr 1fr; border: 1px solid #e0e6f1; border-radius: 28px; overflow: hidden; box-shadow: 0 24px 90px #202b5520; background: #fff; }
.auth-story { position: relative; isolation: isolate; display: flex; flex-direction: column; gap: 64px; padding: clamp(24px, 4vw, 54px); color: #e9efff; background: radial-gradient(ellipse at 0 100%, #353981, transparent 65%), #141c3e; }
.auth-story::after { position: absolute; inset: 0; z-index: -1; content: ''; background: repeating-linear-gradient(90deg, transparent 0 63px, #ffffff07 63px 64px); }
.auth-brand { display: flex; align-items: center; gap: 12px; font-size: 19px; font-weight: 700; }
.auth-brand > span { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: #6269ef; }
.kicker { color: #aab6f6; font-size: 13px; letter-spacing: .16em; }
.story-copy h2 { color: #fff; margin: 12px 0 20px; font-size: clamp(30px, 4.4vw, 52px); line-height: 1.2; letter-spacing: -.045em; }
.story-copy > p:not(.kicker) { color: #c2cbe6; max-width: 28em; line-height: 1.8; }
.story-steps { list-style: none; margin: 34px 0 0; padding: 0; display: grid; gap: 20px; }
.story-steps li { display: flex; gap: 14px; align-items: center; }
.story-steps li > span { padding: 8px 9px; border-radius: 8px; background: #ffffff17; color: #bfc9ff; font: 13px ui-monospace, monospace; }
.story-steps strong, .story-steps small { display: block; }
.story-steps small { margin-top: 4px; color: #bfc8e3; }
.auth-form { min-width: 0; padding: clamp(24px, 3.5vw, 42px); align-self: center; }
.auth-form :deep(.ant-form-item) { margin-bottom: 16px; }
.auth-form :deep(.ant-form-item-label) { padding-bottom: 4px; }
.auth-switch { display: inline-flex; gap: 4px; padding: 4px; border-radius: 10px; background: #f0f2fa; margin-bottom: 18px; }
.auth-switch a { padding: 6px 18px; border-radius: 7px; color: #545d78; }
.auth-switch .router-link-active { background: #fff; color: #4e55d9; font-weight: 650; box-shadow: 0 1px 4px #252c5220; }
.auth-form h1 { margin: 0 0 10px; color: #17223e; font-size: 30px; }
.intro, .help { color: #647089; line-height: 1.7; }
.intro { margin-bottom: 18px; }
.help { margin: 18px 0 0; font-size: 13px; }
.form-message { margin-bottom: 18px; }
.captcha-row { display: flex; align-items: center; gap: 9px; }
.captcha-row :deep(.ant-input) { min-width: 0; }
.captcha-image { display: flex; align-items: center; justify-content: center; gap: 3px; padding: 0 4px; width: 148px; height: 46px; flex: 0 0 148px; overflow: hidden; border: 1px solid #d5def1; border-radius: 8px; background: #edf2fb; color: #344c82; cursor: pointer; font-size: 12px; }
.captcha-image img { min-width: 0; width: calc(100% - 15px); height: auto; }
.refresh-icon { flex-shrink: 0; }
.captcha-image:disabled { cursor: wait; }
.captcha-help { margin: 5px 0 0; color: #647089; font-size: 12px; }
@media (max-width: 780px) {
  .auth-shell { max-width: 500px; grid-template-columns: 1fr; border-radius: 20px; }
  .auth-story { padding: 18px 24px; }
  .story-copy { display: none; }
}
@media (max-width: 380px) {
  .auth-form { padding: 20px; }
  .captcha-image { width: 112px; flex-basis: 112px; }
}
</style>
