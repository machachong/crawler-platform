<template>
  <div class="result-viewer">
    <a-page-header title="提取结果详情" @back="() => $router.back()" />
    <a-row :gutter="24">
      <a-col :span="12">
        <a-card title="清洗后正文 (Raw Context)">
          <div class="raw-content" style="max-height: 600px; overflow-y: auto;">
            {{ result.raw_text }}
          </div>
        </a-card>
      </a-col>
      <a-col :span="12">
        <a-card title="AI 提取结果 (Structured Data)">
          <div class="ai-result">
            <pre style="background: #f4f4f4; padding: 15px; border-radius: 5px;">{{ JSON.stringify(result.extracted_data, null, 2) }}</pre>
          </div>
          <a-divider />
          <a-button type="primary" block>导出为飞书文档</a-button>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup>
import { ref } from 'vue';
const result = ref({
  raw_text: "飞书（Feishu）是字节跳动旗下的企业级协作平台...",
  extracted_data: {
    "product_name": "飞书",
    "core_features": ["即时通讯", "文档", "日历"],
    "summary": "飞书是一款全能的企业协作工具。"
  }
});
</script>
