<template>
  <a-space wrap align="start">
    <a-select :value="modelValue.schedule" :options="cadences" style="width: 140px" @update:value="change('schedule', $event)" />
    <template v-if="modelValue.schedule !== 'once'">
      <a-input type="time" :value="modelValue.schedule_time" aria-label="执行时间" style="width: 140px" @update:value="change('schedule_time', $event)" />
      <a-select v-if="modelValue.schedule === 'weekly'" :value="modelValue.schedule_weekday" :options="weekdays" style="width: 120px" @update:value="change('schedule_weekday', $event)" />
      <a-select :value="modelValue.schedule_timezone" :options="zones" style="width: 180px" @update:value="change('schedule_timezone', $event)" />
    </template>
  </a-space>
</template>
<script setup>
const props = defineProps({ modelValue: { type: Object, required: true } });
const emit = defineEmits(['update:modelValue']);
const cadences = [{ value: 'once', label: '单次运行' }, { value: 'daily', label: '每天' }, { value: 'weekly', label: '每周' }];
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((label, value) => ({ value, label }));
const zones = [{ value: 'Asia/Shanghai', label: '北京时间 UTC+8' }, { value: 'UTC', label: 'UTC' }];
function change(key, value) { emit('update:modelValue', { ...props.modelValue, [key]: value }); }
</script>
