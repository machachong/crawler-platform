<template>
  <div class="creator">
    <a-page-header title="新建抓取任务" sub-title="起始 URL、抓取范围和提取指令" />
    <a-form layout="vertical" :model="formState" @finish="handleFinish">
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

      <a-form-item label="提取预设">
        <a-space wrap>
          <a-button v-for="item in templates" :key="item.key" @click="applyTemplate(item)">
            {{ item.name }}
          </a-button>
        </a-space>
        <p class="hint">可以指定提取什么、从页面哪一部分提取，以及输出成 JSON、表格或摘要。</p>
      </a-form-item>

      <a-form-item label="AI 提取指令 (Prompt)" name="prompt">
        <a-textarea
          v-model:value="formState.prompt"
          :rows="8"
          placeholder="例如：提取每条名言的 text 和 author，返回 JSON 数组。"
        />
      </a-form-item>

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
            <a-radio-group v-model:value="formState.schedule">
              <a-radio value="once">单次运行</a-radio>
              <a-radio value="daily">每日定时</a-radio>
            </a-radio-group>
            <p class="hint">每日定时会写入任务配置。当前版本在创建后立即执行一次。</p>
          </a-form-item>
        </a-collapse-panel>
      </a-collapse>

      <a-form-item style="margin-top: 16px">
        <a-button type="primary" html-type="submit" :loading="loading">提交任务</a-button>
      </a-form-item>
    </a-form>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { useRouter } from 'vue-router';
import api, { errorMessage } from '../utils/api';

const router = useRouter();
const loading = ref(false);

const templates = [
  {
    key: 'quotes',
    name: '名言摘录',
    url: 'https://quotes.toscrape.com/',
    mode: 'single',
    prompt: '提取页面中的所有名言，返回 JSON 数组，每项包含 text 和 author。',
  },
  {
    key: 'comments',
    name: '提取评论',
    prompt: '提取页面中的用户评论，返回 JSON 数组，每项包含 author、content，有评分时带上 rating。',
  },
  {
    key: 'prices',
    name: '提取价格表',
    prompt: '提取商品或条目的名称和价格，返回 JSON 数组，每项包含 name 和 price。',
  },
  {
    key: 'summary',
    name: '总结要点',
    prompt: '用中文总结页面要点，返回 JSON：{"title": "", "summary": "", "key_points": []}。',
  },
];

const formState = reactive({
  target_url: 'https://quotes.toscrape.com/',
  mode: 'single',
  prompt: templates[0].prompt,
  max_depth: 2,
  max_pages: 20,
  concurrency: 2,
  respect_robots: true,
  engine: 'http',
  schedule: 'once',
  keywords: '',
});

function applyTemplate(item) {
  formState.prompt = item.prompt;
  if (item.url) formState.target_url = item.url;
  if (item.mode) formState.mode = item.mode;
}

function handleFinish() {
  submit();
}

async function submit() {
  loading.value = true;
  const keywords = formState.keywords
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
  try {
    await api.post('/tasks', {
      target_url: formState.target_url.trim(),
      mode: formState.mode,
      prompt: formState.prompt,
      config: {
        max_depth: formState.max_depth,
        max_pages: formState.max_pages,
        concurrency: formState.concurrency,
        respect_robots: formState.respect_robots,
        engine: formState.engine,
        schedule: formState.schedule,
        keywords,
      },
    });
    message.success('任务已提交');
    router.push('/');
  } catch (error) {
    message.error(errorMessage(error));
  } finally {
    loading.value = false;
  }
}
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
