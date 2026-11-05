<template>
  <div class="creator">
    <a-page-header title="新建抓取任务" sub-title="起始 URL、抓取范围和提取指令" />
    <a-form layout="vertical" :model="formState" @finish="handleFinish">
      <a-alert v-if="copying" type="info" message="已复制配置，提交后创建独立任务；新任务的定时计划默认关闭。" style="margin-bottom: 16px" />
      <a-form-item label="任务名称"><a-input v-model:value="formState.name" :maxlength="120" placeholder="例如：竞品价格每日采集" /></a-form-item>
      <a-form-item label="业务标签"><a-input v-model:value="formState.tags" placeholder="例如：竞品, 价格（逗号分隔，最多 10 个）" /></a-form-item>
      <a-form-item label="目标 URL" name="target_url" :rules="[{ required: true, message: '请输入目标链接' }]">
        <a-input v-model:value="formState.target_url" placeholder="https://quotes.toscrape.com/" />
      </a-form-item>

      <a-form-item label="抓取模式">
        <a-radio-group v-model:value="formState.mode">
          <a-radio value="single">单页模式</a-radio>
          <a-radio value="site">全站模式</a-radio>
        </a-radio-group>
      </a-form-item>

      <a-row :gutter="16" v-if="formState.mode === 'site'">
        <a-col :xs="24" :md="12">
          <a-form-item label="抓取深度（从起始页再跟随的层数，1-5）">
            <a-slider v-model:value="formState.max_depth" :min="1" :max="5" :marks="{ 1: '1', 2: '2', 5: '5' }" />
          </a-form-item>
        </a-col>
        <a-col :xs="24" :md="12">
          <a-form-item label="最多页面">
            <a-input-number v-model:value="formState.max_pages" :min="1" :max="200" style="width: 100%" />
          </a-form-item>
        </a-col>
      </a-row>

      <a-form-item label="采集模板">
        <a-alert v-if="templateError" :message="templateError" type="warning" style="margin-bottom: 8px" />
        <a-space wrap>
          <a-button v-for="item in templates" :key="item.id" @click="applyTemplate(item)">
            {{ item.name }}
          </a-button>
        </a-space>
        <p class="hint">可以提取字段，也可以生成摘要或文章。任务结束后可预览并一键发布为网页。</p>
      </a-form-item>

      <a-form-item label="任务提示词（提取与内容生成要求）" name="prompt" :rules="[{ max: 20000, message: '提示词最多 20000 字' }]">
        <a-textarea
          v-model:value="formState.prompt"
          :rows="8"
          :maxlength="20000"
          show-count
          placeholder="例如：根据页面内容，写一篇中文解读，包含标题、摘要和三个核心观点；使用 Markdown，不编造原文没有的信息。"
        />
        <p class="hint">这段提示词会随任务保存，抓取后用于 AI 内容生成。留空时仅保存正文，也可直接发布。</p>
      </a-form-item>

      <a-form-item label="发布页标题（可选）" name="page_title">
        <a-input v-model:value="formState.page_title" :maxlength="120" placeholder="例如：本周行业观察；发布时还可修改" />
      </a-form-item>
      <a-form-item label="输出字段校验"><OutputFields v-model="formState.output_fields" /></a-form-item>
      <a-form-item label="本次运行 Token 预算"><a-input-number v-model:value="formState.token_budget" :min="1" :max="10000000" /><p class="hint">达到预算后停止后续 AI 请求，保留已生成的内容。</p></a-form-item>

      <a-collapse>
        <a-collapse-panel key="advanced" header="高级配置">
          <a-row :gutter="16">
            <a-col :xs="24" :md="12">
              <a-form-item label="并发数（1-5）">
                <a-slider v-model:value="formState.concurrency" :min="1" :max="5" />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :md="12">
              <a-form-item label="抓取引擎">
                <a-radio-group v-model:value="formState.engine">
                  <a-radio value="http">HTTP（推荐）</a-radio>
                  <a-radio value="stealth">隐匿浏览器</a-radio>
                </a-radio-group>
                <p class="hint">隐匿浏览器需要额外执行 scrapling install 下载 Chromium。</p>
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="遵守 robots.txt">
            <a-switch v-model:checked="formState.respect_robots" />
          </a-form-item>
          <a-form-item label="关键词过滤（可选，逗号分隔；只对命中的页面做 AI 提取）">
            <a-input v-model:value="formState.keywords" placeholder="价格, 评论" />
          </a-form-item>
          <a-form-item label="调度">
            <ScheduleFields :model-value="formState" @update:model-value="Object.assign(formState, $event)" />
            <p class="hint">提交后先执行一次；周期任务按指定时间继续运行，可在结果页暂停或恢复。</p>
          </a-form-item>
        </a-collapse-panel>
      </a-collapse>

      <a-form-item style="margin-top: 16px">
        <a-space><a-button type="primary" html-type="submit" :loading="loading">提交任务</a-button><a-button :loading="loading" @click="submit(true)">先试跑 1 页</a-button></a-space>
        <p class="hint">试跑会创建仅抓取起始页的独立任务，使用相同提示词和字段规则，产生正常 AI 用量。</p>
      </a-form-item>
    </a-form>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { useRoute, useRouter } from 'vue-router';
import OutputFields from '../components/OutputFields.vue';
import ScheduleFields from '../components/ScheduleFields.vue';
import api, { errorMessage } from '../utils/api';

const router = useRouter();
const loading = ref(false);
const route = useRoute();
const copying = ref(false), templateError = ref('');

const templates = ref([]);

const formState = reactive({
  target_url: 'https://quotes.toscrape.com/',
  mode: 'single',
  prompt: '提取页面中的所有名言，返回 JSON 数组，每项包含 text 和 author。',
  name: '', tags: '', template_id: null, template_version: null, output_fields: [], token_budget: 100000,
  page_title: '',
  max_depth: 2,
  max_pages: 20,
  concurrency: 2,
  respect_robots: true,
  engine: 'http',
  schedule: 'once',
  schedule_time: '09:00', schedule_weekday: 0, schedule_timezone: 'Asia/Shanghai',
  keywords: '',
});

function applyTemplate(item) {
  formState.prompt = item.prompt;
  formState.template_id = item.id;
  formState.template_version = item.version;
  formState.output_fields = JSON.parse(JSON.stringify(item.fields || []));
  if (item.url) formState.target_url = item.url;
  if (item.mode) formState.mode = item.mode;
}

function handleFinish() {
  submit();
}

async function submit(preview = false) {
  loading.value = true;
  const keywords = formState.keywords
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
  try {
    const { data } = await api.post('/tasks', {
      target_url: formState.target_url.trim(),
      mode: formState.mode,
      prompt: formState.prompt,
      config: {
        name: (preview ? `试跑 · ${formState.name.trim() || '采集预览'}` : formState.name.trim()).slice(0, 120),
        is_preview: preview,
        tags: formState.tags.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean),
        template_id: formState.template_id, template_version: formState.template_version,
        output_fields: formState.output_fields, token_budget: formState.token_budget,
        page_title: formState.page_title.trim(),
        max_depth: formState.max_depth,
        max_pages: formState.max_pages,
        concurrency: formState.concurrency,
        respect_robots: formState.respect_robots,
        engine: formState.engine,
        schedule: formState.schedule,
        schedule_time: formState.schedule_time, schedule_weekday: formState.schedule_weekday, schedule_timezone: formState.schedule_timezone,
        keywords,
      },
    });
    message.success('任务已提交');
    router.push(`/results/${data.id}`);
  } catch (error) {
    message.error(errorMessage(error));
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try { templates.value = (await api.get('/templates')).data; }
  catch (error) { templateError.value = errorMessage(error); }
  if (typeof route.query.template === 'string') {
    const item = templates.value.find((template) => template.id === route.query.template);
    if (item) applyTemplate(item);
    else message.warning('模板不存在或无权访问');
  }
  if (typeof route.query.copy === 'string') {
    try {
      const { data } = await api.get(`/tasks/${route.query.copy}`);
      Object.assign(formState, data.config, { target_url: data.target_url, mode: data.mode, prompt: data.prompt || '',
        name: `${data.config?.name || '抓取任务'} 副本`.slice(0, 120),
        schedule: 'once',
        tags: (data.config?.tags || []).join(', '), keywords: (data.config?.keywords || []).join(', ') });
      copying.value = true;
    } catch (error) { message.error(errorMessage(error)); }
  }
});
</script>

<style scoped>
.creator {
  max-width: 880px;
  background: #fff;
  padding: 8px 24px 24px;
}
.hint {
  color: #778;
  margin: 8px 0 0;
}
</style>
