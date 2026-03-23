<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider v-model:collapsed="collapsed" collapsible>
      <div class="logo" style="height: 32px; margin: 16px; background: rgba(255, 255, 255, 0.3)" />
      <a-menu v-model:selectedKeys="selectedKeys" theme="dark" mode="inline">
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
      <a-layout-header style="background: #fff; padding: 0 24px">
        <h2 style="margin: 0">自助爬虫平台 (v1.1)</h2>
      </a-layout-header>
      <a-layout-content style="margin: 24px 16px; padding: 24px; background: #fff; min-height: 280px">
        <router-view />
      </a-layout-content>
      <a-layout-footer style="text-align: center">
        Crawler Platform ©2026 Created by OpenClaw Team
      </a-layout-footer>
    </a-layout>
  </a-layout>
</template>

<script setup>
import { ref } from 'vue';
const collapsed = ref(false);
const selectedKeys = ref(['1']);
</script>
