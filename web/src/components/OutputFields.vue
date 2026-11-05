<template>
  <div>
    <div v-for="(field, index) in modelValue" :key="index" class="field-row">
      <a-input :value="field.name" placeholder="字段名，如 price" :maxlength="64" @update:value="change(index, 'name', $event)" />
      <a-select :value="field.type" :options="types" style="min-width: 110px" @update:value="change(index, 'type', $event)" />
      <a-checkbox :checked="field.required" @update:checked="change(index, 'required', $event)">必填</a-checkbox>
      <a-button danger type="text" @click="emit('update:modelValue', modelValue.filter((_, i) => i !== index))">移除</a-button>
    </div>
    <a-button v-if="modelValue.length < 30" size="small" @click="emit('update:modelValue', [...modelValue, { name: '', type: 'string', required: true }])">添加字段</a-button>
    <p class="hint">可选。设置后会检查 JSON 字段类型与必填项，校验不通过的结果会标记原因。</p>
  </div>
</template>
<script setup>
const props = defineProps({ modelValue: { type: Array, default: () => [] } });
const emit = defineEmits(['update:modelValue']);
const types = [{ value: 'string', label: '文本' }, { value: 'number', label: '数字' }, { value: 'boolean', label: '布尔值' }, { value: 'date', label: '日期' }, { value: 'array', label: '数组' }];
function change(index, key, value) { emit('update:modelValue', props.modelValue.map((field, i) => i === index ? { ...field, [key]: value } : field)); }
</script>
<style scoped>
.field-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.field-row > .ant-input { flex: 1; min-width: 140px; }
.hint { color: #667085; margin: 8px 0 0; font-size: 13px; }
</style>
