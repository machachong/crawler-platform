<template>
  <div>
    <div class="toolbar">
      <div>
        <h3>任务大厅</h3>
        <p>查看抓取、清洗和 AI 提取进度</p>
      </div>
      <a-button type="primary" @click="router.push('/create')">新建抓取</a-button>
    </div>

    <a-spin :spinning="loading">
      <a-empty v-if="!loading && tasks.length === 0" description="还没有任务">
        <a-button type="primary" @click="router.push('/create')">创建第一个任务</a-button>
      </a-empty>
      <a-row :gutter="[16, 16]">
        <a-col v-for="task in tasks" :key="task.id" :xs="24" :md="12" :xl="8">
          <a-card :title="domainOf(task.target_url)" hoverable>
            <template #extra>
              <a-tooltip v-if="task.error_message" :title="task.error_message">
                <a-tag :color="statusMeta(task.status).color">{{ statusMeta(task.status).label }}</a-tag>
              </a-tooltip>
              <a-tag v-else :color="statusMeta(task.status).color">{{ statusMeta(task.status).label }}</a-tag>
            </template>
            <p class="url">{{ task.target_url }}</p>
            <a-progress :percent="task.progress?.percent || 0" :status="progressStatus(task)" />
            <p class="meta">
              {{ task.mode === 'site' ? '全站' : '单页' }}
              · 已抓取 {{ task.pages_crawled || 0 }} 页
              · Token {{ task.token_usage || 0 }}
            </p>
            <p class="meta">{{ formatTime(task.created_at) }}</p>
            <a-alert
              v-if="task.status === 'failed' && task.error_message"
              type="error"
              :message="task.error_message"
              show-icon
              style="margin-bottom: 12px"
            />
            <a-space wrap>
              <a-button size="small" @click="view(task.id)">查看详情</a-button>
              <a-button v-if="isActive(task.status)" size="small" @click="stop(task)">停止</a-button>
              <a-button v-else size="small" @click="rerun(task)">重新运行</a-button>
              <a-popconfirm title="删除这个任务和它的结果？" @confirm="remove(task)">
                <a-button size="small" danger>删除</a-button>
              </a-popconfirm>
            </a-space>
          </a-card>
        </a-col>
      </a-row>
    </a-spin>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import api, { errorMessage } from '../utils/api';
import { domainOf, formatTime, isActive, statusMeta } from '../utils/status';

const router = useRouter();
const tasks = ref([]);
const loading = ref(false);
let timer;

async function load() {
  try {
    const { data } = await api.get('/tasks');
    tasks.value = data;
  } catch (error) {
    message.error(errorMessage(error));
  } finally {
    loading.value = false;
  }
}

function view(id) {
  router.push(`/results/${id}`);
}

async function stop(task) {
  try {
    await api.post(`/tasks/${task.id}/stop`);
    message.success('已请求停止');
    await load();
  } catch (error) {
    message.error(errorMessage(error));
  }
}

async function rerun(task) {
  try {
    await api.post(`/tasks/${task.id}/rerun`);
    message.success('已重新入队');
    await load();
  } catch (error) {
    message.error(errorMessage(error));
  }
}

async function remove(task) {
  try {
    await api.delete(`/tasks/${task.id}`);
    message.success('已删除');
    await load();
  } catch (error) {
    message.error(errorMessage(error));
  }
}

function progressStatus(task) {
  if (task.status === 'failed') return 'exception';
  if (task.status === 'completed') return 'success';
  return 'active';
}

onMounted(() => {
  loading.value = true;
  load();
  timer = setInterval(load, 4000);
});

onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.toolbar h3 {
  margin: 0;
}
.toolbar p {
  margin: 4px 0 0;
  color: #667;
}
.url {
  color: #444;
  word-break: break-all;
  min-height: 44px;
}
.meta {
  color: #778;
  margin-bottom: 4px;
}
</style>
