<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider v-model:collapsed="collapsed" collapsible>
      <div class="brand">抓取台</div>
      <a-menu v-model:selectedKeys="selectedKeys" theme="dark" mode="inline" @click="onMenu">
        <a-menu-item key="1">
          <dashboard-outlined />
          <span>任务大厅</span>
        </a-menu-item>
        <a-menu-item key="2">
          <plus-circle-outlined />
          <span>新建抓取</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>
    <a-layout>
      <a-layout-header class="header">
        <h2>自助爬虫平台</h2>
        <span>URL + Prompt → 抓取、清洗、结构化</span>
      </a-layout-header>
      <a-layout-content class="content">
        <router-view />
      </a-layout-content>
      <a-layout-footer style="text-align: center">
        仅抓取你有权访问的页面，默认遵守 robots.txt
      </a-layout-footer>
    </a-layout>
  </a-layout>
</template>

<script setup>
import { ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { DashboardOutlined, PlusCircleOutlined } from '@ant-design/icons-vue';

const route = useRoute();
const router = useRouter();
const collapsed = ref(false);
const selectedKeys = ref(['1']);

watch(
  () => route.path,
  (path) => {
    selectedKeys.value = path.startsWith('/create') ? ['2'] : ['1'];
  },
  { immediate: true },
);

function onMenu({ key }) {
  router.push(key === '2' ? '/create' : '/');
}
</script>

<style scoped>
.brand {
  height: 48px;
  margin: 12px 16px;
  color: #fff;
  font-weight: 700;
  letter-spacing: 0.08em;
  display: flex;
  align-items: center;
}
.header {
  background: #fff;
  padding: 0 24px;
  display: flex;
  align-items: baseline;
  gap: 16px;
}
.header h2 {
  margin: 0;
}
.header span {
  color: #667;
}
.content {
  margin: 16px;
  min-height: 280px;
}
</style>
