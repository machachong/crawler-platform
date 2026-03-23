<template>
  <div class="dashboard">
    <a-page-header title="任务大厅" sub-title="实时监控爬取与 AI 提取进度" />
    <a-table :dataSource="tasks" :columns="columns" rowKey="id">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="getStatusColor(record.status)">
            {{ record.status.toUpperCase() }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button type="link" @click="viewResults(record.id)">查看结果</a-button>
        </template>
      </template>
    </a-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';

const router = useRouter();
const tasks = ref([
  { id: '1', target_url: 'https://feishu.cn', status: 'completed', created_at: '2026-03-23 18:00' },
  { id: '2', target_url: 'https://github.com', status: 'running', created_at: '2026-03-23 18:10' },
]);

const columns = [
  { title: '任务 ID', dataIndex: 'id', key: 'id' },
  { title: '目标 URL', dataIndex: 'target_url', key: 'target_url' },
  { title: '当前状态', key: 'status' },
  { title: '创建时间', dataIndex: 'created_at', key: 'created_at' },
  { title: '操作', key: 'action' },
];

const getStatusColor = (status) => {
  const colors = {
    pending: 'orange',
    running: 'blue',
    ai_processing: 'purple',
    completed: 'green',
    failed: 'red',
  };
  return colors[status] || 'default';
};

const viewResults = (id) => {
  router.push(`/results/${id}`);
};
</script>
