<template>
  <a-card title="运行记录与计划" style="margin-bottom: 16px">
    <a-alert v-if="error" :message="error" type="error" show-icon style="margin-bottom: 12px" />
    <a-alert v-if="plan && !plan.paused && !schedulerOnline" type="warning" message="定时服务未就绪，周期任务暂不会执行。请联系管理员启动调度服务。" show-icon style="margin-bottom: 12px" />
    <a-space wrap style="margin-bottom: 12px">
      <a-select :value="selectedRun || ''" :options="runOptions" style="width: min(420px, 75vw)" aria-label="选择运行记录" @update:value="emit('select-run', $event)" />
      <a-button v-if="hasMore" size="small" @click="loadRuns(true)">更多历史</a-button>
      <a-button v-if="canWrite()" :disabled="isActive(task.status)" @click="openReextract">修改提示词并重新提取</a-button>
      <a-button v-if="canWrite() && hasFailures" :disabled="isActive(task.status)" :loading="saving" @click="retryFailures">仅重试失败页面</a-button>
      <a-button v-if="canWrite()" @click="openTemplate">存为我的模板</a-button>
      <a-button v-if="canWrite()" @click="openSchedule">设置定时计划</a-button>
    </a-space>
    <p v-if="selected" class="meta">{{ triggerLabels[selected.trigger] || selected.trigger }} · {{ selected.pages_crawled }} 页 · {{ selected.token_usage }} Token<span v-if="selected.metrics?.duration_seconds != null"> · 总耗时 {{ selected.metrics.duration_seconds }} 秒</span><span v-if="selected.configuration?.ai_model"> · {{ selected.configuration.ai_model }}</span></p>
    <a-space v-if="selected?.metrics?.phase_seconds" wrap class="meta"><span v-for="(seconds, phase) in selected.metrics.phase_seconds" :key="phase">{{ phaseLabels[phase] || phase }} {{ seconds }} 秒</span></a-space>
    <p v-if="plan" class="meta">{{ plan.paused ? '计划已暂停' : `下次执行：${formatTime(plan.next_run_at)}` }} · {{ plan.timezone }}<a-button v-if="canWrite()" type="link" size="small" :loading="saving" @click="togglePlan">{{ plan.paused ? '恢复计划' : '暂停计划' }}</a-button></p>
    <p v-else class="meta">单次任务，未启用定时计划。</p>
    <a-collapse v-if="events.length"><a-collapse-panel key="events" header="近期调度记录"><p v-for="event in events" :key="event.scheduled_at" class="meta">{{ formatTime(event.scheduled_at) }} · {{ event.status === 'skipped' ? event.reason : '已创建运行' }}</p></a-collapse-panel></a-collapse>
    <a-modal v-model:open="scheduleOpen" title="定时计划" :footer="null">
      <ScheduleFields v-model="scheduleForm" />
      <p class="meta" style="margin-top: 16px">上次运行未结束时跳过本次；服务恢复后只补最近一期。</p>
      <a-button type="primary" :loading="saving" @click="saveSchedule">保存计划</a-button>
    </a-modal>
    <a-modal v-model:open="reextractOpen" title="复用正文重新提取" :footer="null" :width="720" :mask-closable="false">
      <a-form layout="vertical" :model="extractForm" @finish="reextract">
        <a-form-item label="新提示词" name="prompt" :rules="[{ required: true, whitespace: true, message: '请输入提示词' }]"><a-textarea v-model:value="extractForm.prompt" :rows="7" :maxlength="20000" /></a-form-item>
        <a-form-item label="字段校验"><OutputFields v-model="extractForm.output_fields" /></a-form-item>
        <p class="meta">复用已保存正文，不重新下载网页；会产生新的 AI 用量和运行记录，原结果保留在历史中。</p>
        <a-button type="primary" html-type="submit" :loading="saving">开始重新提取</a-button>
      </a-form>
    </a-modal>
    <a-modal v-model:open="templateOpen" title="存为我的模板" :footer="null">
      <a-form :model="templateForm" layout="vertical" @finish="saveTemplate">
        <a-form-item label="模板名称" name="name" :rules="[{ required: true, whitespace: true, message: '请输入名称' }]"><a-input v-model:value="templateForm.name" :maxlength="120" /></a-form-item>
        <a-form-item label="使用说明"><a-textarea v-model:value="templateForm.description" :maxlength="1000" /></a-form-item>
        <p class="meta">保存当前任务的提示词与字段定义，仅你的账号可以使用此模板。</p>
        <a-button type="primary" html-type="submit" :loading="saving">保存模板</a-button>
      </a-form>
    </a-modal>
  </a-card>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import api, { errorMessage } from '../utils/api';
import { canWrite } from '../utils/auth';
import { formatTime, isActive, statusMeta } from '../utils/status';
import OutputFields from './OutputFields.vue';
import ScheduleFields from './ScheduleFields.vue';

const props = defineProps({ task: { type: Object, required: true }, selectedRun: { type: String, default: '' } });
const emit = defineEmits(['select-run', 'refresh']);
const runs = ref([]), hasMore = ref(false), plan = ref(null), events = ref([]), error = ref(''), saving = ref(false);
const schedulerOnline = ref(false);
const scheduleOpen = ref(false), reextractOpen = ref(false), templateOpen = ref(false);
const scheduleForm = ref({ schedule: 'once', schedule_time: '09:00', schedule_weekday: 0, schedule_timezone: 'Asia/Shanghai' });
const extractForm = reactive({ prompt: '', output_fields: [] }), templateForm = reactive({ name: '', description: '' });
const triggerLabels = { create: '首次运行', preview: '样本试跑', retry: '失败重试', manual: '手动重跑', schedule: '定时执行', reextract: '复用正文', legacy: '历史任务' };
const hasFailures = computed(() => props.task.results?.some((row) => ['fetch_failed', 'ai_failed', 'validation_failed', 'ai_pending'].includes(row.status)));
const phaseLabels = { pending: '排队', crawling: '抓取', cleaning: '清洗', ai_processing: 'AI 处理' };
const selected = computed(() => props.selectedRun ? runs.value.find((run) => run.id === props.selectedRun) : runs.value[0]);
const runOptions = computed(() => [{ value: '', label: '最新运行' }, ...runs.value.map((run) => ({ value: run.id,
  label: `第 ${run.number} 次 · ${statusMeta(run.status).label} · ${formatTime(run.created_at)}` }))]);

async function loadRuns(append = false) {
  const id = props.task.id;
  try {
    const { data } = await api.get(`/tasks/${id}/runs`, { params: { limit: 20, offset: append ? runs.value.length : 0 } });
    if (id !== props.task.id) return;
    runs.value = append ? [...runs.value, ...data] : data; hasMore.value = data.length === 20;
  } catch (cause) { if (id === props.task.id) error.value = errorMessage(cause); }
}
async function refresh() {
  error.value = ''; const id = props.task.id;
  await loadRuns();
  try {
    const [schedule, history, service] = await Promise.all([api.get(`/tasks/${id}/schedule`), api.get(`/tasks/${id}/schedule-events`), api.get('/service-status')]);
    if (id === props.task.id) { plan.value = schedule.data; events.value = history.data; schedulerOnline.value = service.data.scheduler_online; }
  } catch (cause) { if (id === props.task.id) error.value = errorMessage(cause); }
}
watch(() => [props.task.id, props.task.status, props.task.updated_at], refresh, { immediate: true });
function openSchedule() {
  scheduleForm.value = { schedule: plan.value?.cadence || 'once', schedule_time: plan.value?.time_of_day || '09:00', schedule_weekday: plan.value?.weekday || 0, schedule_timezone: plan.value?.timezone || 'Asia/Shanghai' };
  scheduleOpen.value = true;
}
async function saveSchedule() {
  saving.value = true;
  try { plan.value = (await api.put(`/tasks/${props.task.id}/schedule`, scheduleForm.value)).data; scheduleOpen.value = false; message.success('计划已保存'); }
  catch (cause) { message.error(errorMessage(cause)); }
  finally { saving.value = false; }
}
async function togglePlan() {
  saving.value = true;
  try { plan.value = (await api.put(`/tasks/${props.task.id}/schedule`, { schedule: plan.value.cadence, schedule_time: plan.value.time_of_day, schedule_weekday: plan.value.weekday, schedule_timezone: plan.value.timezone, paused: !plan.value.paused })).data; }
  catch (cause) { message.error(errorMessage(cause)); }
  finally { saving.value = false; }
}
function openReextract() { extractForm.prompt = props.task.prompt || ''; extractForm.output_fields = JSON.parse(JSON.stringify(props.task.config?.output_fields || [])); reextractOpen.value = true; }
async function retryFailures() {
  saving.value = true;
  try { await api.post(`/tasks/${props.task.id}/retry-failed`); emit('select-run', ''); emit('refresh'); message.success('已创建失败页面重试运行'); }
  catch (cause) { message.error(errorMessage(cause)); } finally { saving.value = false; }
}
async function reextract() {
  saving.value = true;
  try { await api.post(`/tasks/${props.task.id}/reextract`, extractForm); reextractOpen.value = false; emit('select-run', ''); emit('refresh'); message.success('已创建新运行，复用保存的正文'); }
  catch (cause) { message.error(errorMessage(cause)); }
  finally { saving.value = false; }
}
function openTemplate() { templateForm.name = props.task.config?.name || '我的采集模板'; templateForm.description = ''; templateOpen.value = true; }
async function saveTemplate() {
  saving.value = true;
  try { await api.post('/templates', { ...templateForm, prompt: props.task.prompt, fields: props.task.config?.output_fields || [] }); templateOpen.value = false; message.success('已保存到采集模板'); }
  catch (cause) { message.error(errorMessage(cause)); }
  finally { saving.value = false; }
}
</script>
<style scoped>.meta { color: #667085; font-size: 13px; margin: 8px 0 12px; }</style>
