<template>
  <div>
    <div class="toolbar">
      <div>
        <p class="section-label">采集工作空间</p>
        <h1>任务中心</h1>
        <p>{{ auth.user?.role === 'admin' ? '查看平台全部采集任务' : '查看我的采集任务' }}，追踪运行记录与发布结果。</p>
      </div>
      <a-button v-if="canWrite()" type="primary" @click="router.push('/create')"><plus-outlined />新建抓取</a-button>
    </div>
    <div class="stats">
      <div v-for="item in stats" :key="item.label" class="stat" :class="item.tone">
        <span class="stat-icon"><component :is="item.icon" /></span>
        <span>{{ item.label }}</span>
        <strong>{{ item.value.toLocaleString('zh-CN') }}</strong>
      </div>
    </div>
    <section class="filter-panel" aria-label="任务筛选">
      <div class="filters">
        <a-input-search v-model:value="filters.q" placeholder="搜索名称、URL 或标签" allow-clear @search="apply" />
        <a-select v-model:value="filters.status" :options="statusOptions" aria-label="任务状态" @change="apply" />
        <a-button @click="reset">重置</a-button>
      </div>
      <details :open="Boolean(filters.domain || filters.from || filters.to)">
        <summary>高级筛选</summary>
        <div class="advanced-filters">
          <a-input v-model:value="filters.domain" placeholder="目标域名，如 example.com" aria-label="目标域名" allow-clear @press-enter="apply" />
          <label>开始日期<a-input v-model:value="filters.from" type="date" /></label>
          <label>结束日期<a-input v-model:value="filters.to" type="date" /></label>
          <a-button @click="apply">应用筛选</a-button>
        </div>
      </details>
    </section>
    <div class="list-heading"><h2>任务列表</h2><span>共 {{ total }} 项</span></div>
    <a-alert v-if="error" :message="error" type="error" show-icon style="margin-bottom: 16px" />
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && tasks.length === 0" description="没有符合条件的任务" />
      <a-row :gutter="[16, 16]">
        <a-col v-for="task in tasks" :key="task.id" :xs="24" :md="12" :xl="8">
          <a-card class="task-card" :title="task.config?.name || domainOf(task.target_url)">
            <template #extra><a-tag :color="statusMeta(task.status).color">{{ statusMeta(task.status).label }}</a-tag></template>
            <p class="url" :title="task.target_url">{{ task.target_url }}</p>
            <a-space wrap><a-tag v-for="tag in task.config?.tags || []" :key="tag">{{ tag }}</a-tag></a-space>
            <a-progress :percent="task.progress?.percent || 0" :status="progressStatus(task)" />
            <p class="meta">{{ task.mode === 'site' ? '全站' : '单页' }} · {{ task.pages_crawled || 0 }} 页 · {{ task.token_usage || 0 }} Token</p>
            <p class="meta">{{ task.run_count || 0 }} 次运行 · 创建于 {{ formatTime(task.created_at) }}</p>
            <p v-if="auth.user?.role === 'admin'" class="meta">负责人：{{ task.owner_name }}</p>
            <p v-if="task.last_success_at" class="meta">最近成功：{{ formatTime(task.last_success_at) }}</p>
            <p v-if="task.schedule_plan" class="meta">{{ task.schedule_plan.paused ? '计划已暂停' : `下次执行 ${formatTime(task.schedule_plan.next_run_at)}` }}</p>
            <a-alert v-if="task.error_message" :type="task.status === 'failed' ? 'error' : 'warning'" :message="task.error_message" show-icon class="task-error" />
            <a-space class="task-actions" wrap>
              <a-button size="small" type="primary" ghost @click="router.push(`/results/${task.id}`)">查看结果</a-button>
              <template v-if="canWrite()">
                <a-button v-if="isActive(task.status)" size="small" :loading="busy === task.id" @click="operate(task, 'stop')">停止</a-button>
                <a-button v-else size="small" :loading="busy === task.id" @click="operate(task, 'rerun')">重新运行</a-button>
                <a-button size="small" @click="router.push({ path: '/create', query: { copy: task.id } })">复制配置</a-button>
                <a-popconfirm title="删除任务、全部运行记录和已发布网页？" @confirm="operate(task, 'delete')"><a-button size="small" danger>删除</a-button></a-popconfirm>
              </template>
            </a-space>
          </a-card>
        </a-col>
      </a-row>
    </a-spin>
    <a-pagination v-if="total > 0" :current="page" :page-size="12" :total="total" :show-size-changer="false" :show-total="(count) => `共 ${count} 个任务`" style="margin-top: 24px; text-align: right" @change="changePage" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { CheckCircleOutlined, ClockCircleOutlined, DatabaseOutlined, PlusOutlined, ThunderboltOutlined, WarningOutlined } from '@ant-design/icons-vue';
import api, { errorMessage } from '../utils/api';
import { auth, canWrite } from '../utils/auth';
import { domainOf, formatTime, isActive, statusMeta } from '../utils/status';

const router = useRouter(), route = useRoute();
const tasks = ref([]), total = ref(0), counts = ref({}), loading = ref(true), error = ref(''), busy = ref('');
const usage = ref({ tokens_7d: 0 });
const filters = reactive({ q: '', status: '', domain: '', from: '', to: '' });
const page = computed(() => Math.max(1, Number(route.query.page) || 1));
const stats = computed(() => [
  { label: '任务总数', icon: DatabaseOutlined, tone: 'indigo', value: Object.values(counts.value).reduce((sum, n) => sum + n, 0) },
  { label: '运行中', icon: ClockCircleOutlined, tone: 'blue',
    value: ['pending', 'crawling', 'cleaning', 'ai_processing'].reduce((sum, key) => sum + (counts.value[key] || 0), 0) },
  { label: '已完成', icon: CheckCircleOutlined, tone: 'green', value: counts.value.completed || 0 },
  { label: '失败', icon: WarningOutlined, tone: 'red', value: counts.value.failed || 0 },
  { label: '近 7 天 Token', icon: ThunderboltOutlined, tone: 'amber', value: usage.value.tokens_7d || 0 },
]);
const statusOptions = [{ value: '', label: '全部状态' }, ...['pending', 'crawling', 'cleaning', 'ai_processing', 'completed', 'failed', 'stopped'].map((value) => ({ value, label: statusMeta(value).label }))];
let timer, requestNumber = 0;
const filterStorageKey = `crawler-task-filters-${auth.user?.id || 'anonymous'}`;

function readFilters() { Object.keys(filters).forEach((key) => { filters[key] = typeof route.query[key] === 'string' ? route.query[key] : ''; }); }
function apply() {
  const query = Object.fromEntries(Object.entries(filters).filter(([, value]) => value));
  if (JSON.stringify(query) === JSON.stringify(route.query)) load();
  else router.replace({ query });
}
function reset() { Object.keys(filters).forEach((key) => { filters[key] = ''; }); apply(); }
function changePage(value) { router.replace({ query: { ...route.query, page: value } }); }
async function load() {
  const number = ++requestNumber;
  const params = { q: route.query.q || '', status: route.query.status || '', domain: route.query.domain || '', limit: 12, offset: (page.value - 1) * 12 };
  try {
    if (route.query.from) params.created_after = new Date(`${route.query.from}T00:00:00+08:00`).toISOString();
    if (route.query.to) { const date = new Date(`${route.query.to}T00:00:00+08:00`); date.setUTCDate(date.getUTCDate() + 1); params.created_before = date.toISOString(); }
    const { data } = await api.get('/task-center', { params });
    if (number !== requestNumber) return;
    tasks.value = data.items; total.value = data.total; counts.value = data.counts; usage.value = data.usage; error.value = '';
    try { sessionStorage.setItem(filterStorageKey, JSON.stringify(route.query)); } catch { /* Filtering works without persistence. */ }
    if (!data.items.length && page.value > 1) changePage(Math.max(1, Math.ceil(data.total / 12)));
  } catch (cause) { if (number === requestNumber) error.value = errorMessage(cause); }
  finally { if (number === requestNumber) loading.value = false; }
}
async function operate(task, action) {
  if (busy.value) return;
  busy.value = task.id;
  try {
    if (action === 'delete') await api.delete(`/tasks/${task.id}`);
    else await api.post(`/tasks/${task.id}/${action}`);
    message.success(action === 'delete' ? '已删除' : action === 'stop' ? '已停止，保留已有结果' : '已创建新的运行，旧结果保留在历史中');
    await load();
  } catch (cause) { message.error(errorMessage(cause)); }
  finally { busy.value = ''; }
}
function progressStatus(task) { return task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : 'active'; }
watch(() => route.query, () => { readFilters(); load(); });
onMounted(() => {
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(filterStorageKey) || '{}'); } catch { saved = {}; }
  if (!Object.keys(route.query).length && Object.keys(saved).length) router.replace({ query: saved });
  else { readFilters(); load(); }
  timer = setInterval(load, 5000);
});
onUnmounted(() => { requestNumber++; clearInterval(timer); });
</script>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: 16px; margin-bottom: 24px; padding: 24px; border: 1px solid #e0e7f7; border-radius: 16px; background: linear-gradient(112deg, #fff, #eef0ff); }
.toolbar h1 { margin: 8px 0; color: #182442; font-size: clamp(24px, 3vw, 36px); letter-spacing: -.04em; }
.toolbar p { margin: 0; color: #62718a; line-height: 1.65; }
.toolbar .section-label { color: #4e55b9; font-size: 11px; letter-spacing: .12em; font-weight: 700; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 126px), 1fr)); gap: 12px; margin-bottom: 20px; }
.stat { display: grid; gap: 7px; padding: 15px; border: 1px solid #e4e9f4; border-radius: 14px; background: #fff; }
.stat > span:not(.stat-icon) { color: #62718a; font-size: 12px; }
.stat strong { color: #233050; font-size: 25px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.stat-icon { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 8px; background: #ebeaff; color: #5157c9; }
.blue .stat-icon { background: #e8f2ff; color: #2865a5; }
.green .stat-icon { background: #e6f5ee; color: #277a56; }
.red .stat-icon { background: #fcecef; color: #ad425a; }
.amber .stat-icon { background: #fff3df; color: #946216; }
.filter-panel { margin-bottom: 24px; padding: 16px; border: 1px solid #e4e9f4; border-radius: 14px; background: #fff; }
.filters, .advanced-filters { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.filters > .ant-input-search { flex: 1 1 240px; min-width: 0; }
.filters > .ant-select { min-width: 114px; }
.filter-panel details { margin-top: 12px; }
.filter-panel summary { width: fit-content; cursor: pointer; color: #4b55a8; font-size: 13px; }
.advanced-filters { margin-top: 12px; }
.advanced-filters > .ant-input-affix-wrapper { flex: 1 1 180px; }
.advanced-filters label { display: flex; align-items: center; gap: 6px; color: #62718a; font-size: 12px; }
.advanced-filters label .ant-input { width: auto; }
.list-heading { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; color: #62718a; }
.list-heading h2 { margin: 0; color: #202c48; font-size: 18px; }
.task-card { border-color: #e4e9f4; border-radius: 14px; }
.task-card:hover { border-color: #bcc8ef; }
.url { color: #596b89; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; margin-bottom: 8px; }
.meta { color: #62718a; font-size: 13px; margin-bottom: 6px; overflow-wrap: anywhere; }
.task-error { margin: 12px 0; max-height: 110px; overflow: auto; }
.task-actions { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ebeff8; width: 100%; }
@media (max-width: 600px) {
  .toolbar { padding: 16px; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat { grid-template-columns: minmax(0, 1fr) auto; align-items: center; padding: 10px; }
  .stat-icon { display: none; }
  .stat strong { font-size: 20px; }
  .advanced-filters label { flex-wrap: wrap; }
}
</style>
