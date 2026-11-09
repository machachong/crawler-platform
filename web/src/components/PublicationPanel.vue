<template>
  <a-card class="publication-card" title="网页预览与发布">
    <p class="hint">先预览当前结果，再生成可以分享的网页链接。</p>
    <a-alert v-if="error" type="error" :message="error" show-icon style="margin-bottom: 12px" />
    <a-space wrap style="margin-bottom: 12px">
      <a-input v-model:value="title" :maxlength="120" aria-label="网页标题" placeholder="网页标题" style="width: min(320px, 65vw)" />
      <a-button :disabled="!ready || busy" :loading="previewing" @click="preview">预览网页</a-button>
      <a-button v-if="canWrite()" type="primary" :disabled="!ready || busy || loading" :loading="publishing" @click="publish">
        {{ publication ? '更新发布' : '一键发布 HTML' }}
      </a-button>
      <a-popconfirm v-if="publication && canWrite()" title="撤下后，原链接将无法访问。确定撤下？" @confirm="unpublish">
        <a-button danger :disabled="busy" :loading="withdrawing">撤下网页</a-button>
      </a-popconfirm>
    </a-space>
    <p v-if="!ready" class="hint">等待任务结束或停止后，即可预览和发布已有结果。</p>
    <div v-if="publication" class="published">
      <a-tag color="green">已发布</a-tag><span>{{ publication.title }}</span>
      <p class="hint">更新于 {{ formatTime(publication.updated_at) }}。重跑任务不会覆盖线上版本，点击「更新发布」后生效。</p>
      <div class="link-row">
        <input ref="linkInput" class="public-link" :value="publication.url" readonly aria-label="公开网页链接" />
        <a-button @click="copyLink">复制链接</a-button>
        <a :href="publication.url" target="_blank" rel="noopener noreferrer">打开网页</a>
      </div>
    </div>
    <a-modal v-model:open="previewOpen" title="当前结果 · 网页预览" :footer="null"
      :width="'min(1120px, calc(100vw - 24px))'" destroy-on-close @after-close="previewHtml = ''">
      <iframe :srcdoc="previewHtml" title="生成内容的网页预览" sandbox="allow-popups allow-popups-to-escape-sandbox" class="preview-frame" />
    </a-modal>
  </a-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import api, { errorMessage } from '../utils/api';
import { canWrite } from '../utils/auth';
import { formatTime, isActive } from '../utils/status';

const props = defineProps({ task: { type: Object, required: true } });
const title = ref(''), error = ref(''), publication = ref(null), linkInput = ref(null);
const loading = ref(false), previewing = ref(false), publishing = ref(false), withdrawing = ref(false);
const previewOpen = ref(false), previewHtml = ref('');
const busy = computed(() => previewing.value || publishing.value || withdrawing.value);
const ready = computed(() => !isActive(props.task.status) && props.task.results?.length > 0);

watch(() => props.task.id, async (id) => {
  publication.value = null; previewOpen.value = false; previewHtml.value = ''; error.value = '';
  title.value = (props.task.config?.page_title || `采集报告 · ${new URL(props.task.target_url).hostname}`).slice(0, 120);
  loading.value = true;
  try {
    const { data } = await api.get(`/tasks/${id}/publication`);
    if (props.task.id === id) {
      publication.value = data;
      if (data) title.value = data.title;
    }
  } catch (cause) { if (props.task.id === id) error.value = errorMessage(cause); }
  finally { if (props.task.id === id) loading.value = false; }
}, { immediate: true });

async function preview() {
  previewing.value = true; error.value = '';
  const id = props.task.id;
  try {
    const { data, headers } = await api.get(`/tasks/${id}/preview`, {
      params: { title: title.value.trim() }, responseType: 'arraybuffer',
    });
    if (!headers['content-type']?.toLowerCase().startsWith('text/html')) {
      throw new Error('预览接口返回了非网页内容，请刷新后重试。');
    }
    let html;
    try {
      html = new TextDecoder('utf-8', { fatal: true }).decode(data);
    } catch {
      throw new Error('预览返回的网页编码异常，请检查服务端或反向代理的 UTF-8 配置。');
    }
    if (props.task.id === id) { previewHtml.value = html; previewOpen.value = true; }
  } catch (cause) { if (props.task.id === id) error.value = errorMessage(cause); }
  finally { previewing.value = false; }
}
async function publish() {
  publishing.value = true; error.value = '';
  const id = props.task.id;
  try {
    const { data } = await api.post(`/tasks/${id}/publication`, { title: title.value.trim() });
    if (props.task.id === id) { publication.value = data; message.success('网页已发布'); }
  } catch (cause) { if (props.task.id === id) error.value = errorMessage(cause); }
  finally { publishing.value = false; }
}
async function unpublish() {
  withdrawing.value = true; error.value = '';
  const id = props.task.id;
  try {
    await api.delete(`/tasks/${id}/publication`);
    if (props.task.id === id) { publication.value = null; message.success('网页已撤下，原链接已失效'); }
  } catch (cause) { if (props.task.id === id) error.value = errorMessage(cause); }
  finally { withdrawing.value = false; }
}
async function copyLink() {
  try {
    await navigator.clipboard.writeText(publication.value.url);
    message.success('链接已复制');
  } catch {
    linkInput.value?.select();
    message.info('请复制已选中的链接');
  }
}
</script>

<style scoped>
.hint { color: #667085; margin: 0 0 12px; font-size: 13px; }
.publication-card { margin-bottom: 16px; border-color: #dae3f5; background: #f9fbff; }
.published { margin-top: 8px; }
.published > .hint { margin-top: 8px; }
.link-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.public-link { flex: 1; min-width: 200px; border: 1px solid #d9d9d9; border-radius: 6px; padding: 6px 10px; color: #176b55; background: #fafafa; }
.preview-frame { display: block; width: 100%; height: 70dvh; border: 1px solid #e4e7ec; border-radius: 10px; background: #f8fafc; }
</style>
