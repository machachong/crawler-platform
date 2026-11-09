<template>
  <div>
    <a-page-header :title="task?.config?.name || '提取结果'" :sub-title="task?.target_url || ''" @back="() => router.push('/')" />
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && !task" description="任务不存在" />
      <template v-else-if="task">
        <TaskOperations :task="currentTask || task" :selected-run="selectedRun" @select-run="selectRun" @refresh="load" />
        <a-alert v-if="selectedRun" type="info" message="正在查看历史运行结果。返回最新运行后可以发布网页。" show-icon style="margin-bottom: 12px" />
        <a-space wrap style="margin-bottom: 12px">
          <a-tag :color="statusMeta(task.status).color">{{ statusMeta(task.status).label }}</a-tag>
          <span>页面 {{ task.pages_crawled || 0 }}</span>
          <span>Token {{ task.token_usage || 0 }}</span>
          <a-button size="small" @click="download('json')">导出 JSON</a-button>
          <a-button size="small" @click="download('csv')">导出 CSV</a-button>
          <a-button size="small" @click="download('md')">导出 Markdown</a-button>
          <a-button size="small" :disabled="isActive(task.status) || !results.length" @click="download('html')">下载 HTML</a-button>
        </a-space>
        <PublicationPanel v-if="!selectedRun" :task="task" />
        <a-collapse style="margin-bottom: 16px">
          <a-collapse-panel key="prompt" header="任务提示词">
            <pre class="pane">{{ task.prompt || '未配置提示词，此任务仅抓取正文。' }}</pre>
          </a-collapse-panel>
        </a-collapse>
        <a-alert
          v-if="task.error_message"
          :type="task.status === 'failed' ? 'error' : 'warning'"
          :message="task.error_message"
          show-icon
          style="margin-bottom: 12px"
        />
        <a-progress :percent="task.progress?.percent || 0" />

        <a-empty v-if="results.length === 0" description="还没有页面结果" />
        <template v-else>
          <a-select
            v-if="results.length > 1"
            v-model:value="activeId"
            style="width: 100%; margin: 12px 0"
            :options="results.map((row) => ({ value: row.id, label: row.source_url }))"
          />
          <a-row :gutter="[16, 16]" v-if="active">
            <a-col :xs="24" :lg="12">
              <a-card title="清洗后正文">
                <p class="meta">{{ active.source_url }} · 深度 {{ active.depth }} · {{ resultStatus(active.status) }}</p>
                <pre class="pane">{{ active.raw_text || '（空）' }}</pre>
              </a-card>
            </a-col>
            <a-col :xs="24" :lg="12">
              <a-card :title="`AI 提取结果 · ${active.token_usage || 0} tokens`">
                <a-alert v-if="active.error_message" type="error" :message="active.error_message" show-icon style="margin-bottom: 12px" />
                <a-table
                  v-if="tableRows"
                  :columns="tableColumns"
                  :data-source="tableRows"
                  :pagination="false"
                  size="small"
                  :scroll="{ x: true }"
                />
                <pre v-else class="pane json">{{ pretty(active.extracted_data) }}</pre>
                <a-collapse style="margin-top: 12px">
                  <a-collapse-panel key="json" header="原始 JSON">
                    <pre class="pane json">{{ pretty(active.extracted_data) }}</pre>
                  </a-collapse-panel>
                </a-collapse>
              </a-card>
            </a-col>
          </a-row>
        </template>

        <a-collapse style="margin-top: 16px">
          <a-collapse-panel key="logs" header="原始日志">
            <a-empty v-if="!(task.progress?.logs || []).length" description="暂无日志" />
            <ul v-else class="logs">
              <li v-for="(item, index) in task.progress.logs" :key="index">
                <span>{{ item.time }}</span>
                {{ item.message }}
              </li>
            </ul>
          </a-collapse-panel>
        </a-collapse>
      </template>
    </a-spin>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import api, { errorMessage } from '../utils/api';
import { isActive, resultStatus, statusMeta } from '../utils/status';
import PublicationPanel from '../components/PublicationPanel.vue';
import TaskOperations from '../components/TaskOperations.vue';

const route = useRoute();
const router = useRouter();
const task = ref(null);
const currentTask = ref(null);
const selectedRun = computed(() => typeof route.query.run === 'string' ? route.query.run : '');
const loading = ref(true);
const activeId = ref(null);
let timer;
let requestNumber = 0;

const results = computed(() => task.value?.results || []);
const active = computed(() => results.value.find((row) => row.id === activeId.value) || results.value[0] || null);

const tableRows = computed(() => {
  const data = active.value?.extracted_data;
  if (!Array.isArray(data) || !data.length || typeof data[0] !== 'object' || Array.isArray(data[0])) {
    return null;
  }
  return data.map((row, index) => {
    const flat = { key: index };
    Object.entries(row).forEach(([key, value]) => {
      flat[key] = value != null && typeof value === 'object' ? JSON.stringify(value) : value;
    });
    return flat;
  });
});

const tableColumns = computed(() => {
  if (!tableRows.value) return [];
  const keys = [...new Set(tableRows.value.flatMap((row) => Object.keys(row).filter((key) => key !== 'key')))];
  return keys.map((key) => ({ title: key, dataIndex: key, key }));
});

function pretty(value) {
  if (value == null) return '（尚未提取）';
  return JSON.stringify(value, null, 2);
}

async function load() {
  const number = ++requestNumber;
  try {
    const latest = await api.get(`/tasks/${route.params.id}`);
    const { data } = selectedRun.value ? await api.get(`/tasks/${route.params.id}`, { params: { run_id: selectedRun.value } }) : latest;
    if (number !== requestNumber) return;
    currentTask.value = latest.data;
    task.value = data;
    if (!activeId.value && data.results?.length) activeId.value = data.results[0].id;
    if (!isActive(latest.data.status) && timer) {
      clearInterval(timer);
      timer = null;
    }
    if (isActive(latest.data.status) && !timer) timer = setInterval(load, 3000);
  } catch (error) {
    if (number !== requestNumber) return;
    if (error?.response?.status === 404) task.value = null;
    else message.error(errorMessage(error));
  } finally {
    if (number === requestNumber) loading.value = false;
  }
}

async function download(format) {
  try {
    const response = await api.get(`/tasks/${route.params.id}/export`, {
      params: { format, ...(selectedRun.value ? { run_id: selectedRun.value } : {}) },
      responseType: 'arraybuffer',
    });
    const blob = new Blob([response.data], { type: response.headers['content-type'] });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `task-${route.params.id}${selectedRun.value ? `-run-${selectedRun.value}` : ''}.${format === 'md' ? 'md' : format}`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    message.error(errorMessage(error));
  }
}

function selectRun(run) { router.replace({ query: run ? { ...route.query, run } : Object.fromEntries(Object.entries(route.query).filter(([key]) => key !== 'run')) }); }
watch(() => [route.params.id, selectedRun.value], () => { activeId.value = null; loading.value = true; load(); });
onMounted(() => {
  load();
  timer = setInterval(load, 3000);
});
onUnmounted(() => {
  requestNumber++;
  if (timer) clearInterval(timer);
});
</script>

<style scoped>
.pane {
  max-height: 560px;
  overflow: auto;
  background: #f7f8fa;
  padding: 12px;
  border-radius: 6px;
}
.json {
  background: #111827;
  color: #e5e7eb;
}
.meta {
  color: #667;
  word-break: break-all;
}
.logs {
  padding-left: 18px;
}
.logs span {
  color: #889;
  margin-right: 8px;
}
</style>
