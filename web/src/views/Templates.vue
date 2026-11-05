<template>
  <div>
    <div class="toolbar"><div><h2>采集模板</h2><p>复用提示词与字段规则，任务运行会记录实际使用的模板版本。</p></div><a-button v-if="canWrite()" type="primary" @click="open()">新建我的模板</a-button></div>
    <a-alert v-if="error" :message="error" type="error" show-icon style="margin-bottom: 16px" />
    <a-spin :spinning="loading"><a-row :gutter="[16, 16]"><a-col v-for="item in items" :key="item.id" :xs="24" :md="12" :xl="8"><a-card :title="item.name">
      <template #extra><a-tag>{{ item.is_system ? '内置' : '我的' }} · v{{ item.version }}</a-tag></template>
      <p>{{ item.description || '自定义内容生成模板' }}</p><pre class="prompt">{{ item.prompt }}</pre>
      <p class="meta">{{ item.fields.length }} 个校验字段</p>
      <a-space v-if="canWrite()"><a-button type="primary" @click="router.push({ path: '/create', query: { template: item.id } })">使用模板</a-button><a-button v-if="!item.is_system" @click="open(item)">编辑新版本</a-button></a-space>
    </a-card></a-col></a-row></a-spin>
    <a-modal v-model:open="opened" :title="editing ? '编辑模板，保存新版本' : '新建我的模板'" :footer="null" :width="720" :mask-closable="false">
      <a-form :key="formKey" :model="form" layout="vertical" @finish="save">
        <a-form-item label="名称" name="name" :rules="[{ required: true, whitespace: true, message: '请输入名称' }]"><a-input v-model:value="form.name" :maxlength="120" /></a-form-item>
        <a-form-item label="使用说明"><a-input v-model:value="form.description" :maxlength="1000" /></a-form-item>
        <a-form-item label="提示词" name="prompt" :rules="[{ required: true, whitespace: true, message: '请输入提示词' }]"><a-textarea v-model:value="form.prompt" :rows="7" :maxlength="20000" /></a-form-item>
        <a-form-item label="输出字段"><OutputFields v-model="form.fields" /></a-form-item>
        <p class="meta">模板只对你的账号可见。修改不会改变已经创建的任务与历史结果。</p>
        <a-button type="primary" html-type="submit" :loading="saving">保存</a-button>
      </a-form>
    </a-modal>
  </div>
</template>
<script setup>
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import api, { errorMessage } from '../utils/api';
import { canWrite } from '../utils/auth';
import OutputFields from '../components/OutputFields.vue';
const router = useRouter();
const items = ref([]), error = ref(''), loading = ref(true), saving = ref(false), opened = ref(false), editing = ref(null), formKey = ref(0);
const form = reactive({ name: '', description: '', prompt: '', fields: [] });
async function load() { try { items.value = (await api.get('/templates')).data; error.value = ''; } catch (cause) { error.value = errorMessage(cause); } finally { loading.value = false; } }
function open(item) { editing.value = item || null; Object.assign(form, item ? { name: item.name, description: item.description, prompt: item.prompt, fields: JSON.parse(JSON.stringify(item.fields)) } : { name: '', description: '', prompt: '', fields: [] }); formKey.value++; opened.value = true; }
async function save() {
  saving.value = true;
  try { if (editing.value) await api.put(`/templates/${editing.value.id}`, form); else await api.post('/templates', form); opened.value = false; message.success('模板已保存'); await load(); }
  catch (cause) { message.error(errorMessage(cause)); } finally { saving.value = false; }
}
onMounted(load);
</script>
<style scoped>
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; }.toolbar h2 { margin: 0 0 8px; }.toolbar p, .meta { color: #667085; font-size: 13px; }.prompt { max-height: 160px; overflow: auto; white-space: pre-wrap; background: #f7f8fa; padding: 12px; }
</style>
