<template>
  <div class="task-creator">
    <a-page-header title="新建抓取任务" sub-title="输入 URL 和 Prompt，开始智能抓取" />
    <a-form :model="formState" layout="vertical" @finish="handleFinish">
      <a-form-item label="目标 URL" name="url" :rules="[{ required: true, message: '请输入目标链接' }]">
        <a-input v-model:value="formState.url" placeholder="https://example.com" />
      </a-form-item>
      
      <a-form-item label="抓取模式">
        <a-radio-group v-model:value="formState.mode">
          <a-radio value="single">单页模式</a-radio>
          <a-radio value="site">全站模式</a-radio>
        </a-radio-group>
      </a-form-item>

      <a-form-item label="AI 提取指令 (Prompt)" name="prompt">
        <a-textarea 
          v-model:value="formState.prompt" 
          placeholder="例如：提取页面内所有的商品名称和价格，并总结为 JSON 格式..." 
          :rows="6" 
        />
      </a-form-item>

      <a-form-item>
        <a-button type="primary" html-type="submit" :loading="loading">提交任务</a-button>
      </a-form-item>
    </a-form>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { useRouter } from 'vue-router';
import axios from 'axios';

const router = useRouter();
const loading = ref(false);
const formState = reactive({
  url: '',
  mode: 'single',
  prompt: '',
});

const handleFinish = async (values) => {
  loading.value = true;
  try {
    const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';
    await axios.post(`${apiBase}/tasks`, values);
    message.success('任务已提交至队列');
    router.push('/');
  } catch (error) {
    message.error('提交失败: ' + (error.response?.data?.detail || error.message));
  } finally {
    loading.value = false;
  }
};
</script>
