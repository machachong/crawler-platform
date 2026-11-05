export const STATUS_META = {
  pending: { label: '排队中', color: 'orange' },
  crawling: { label: '抓取中', color: 'blue' },
  cleaning: { label: '清洗中', color: 'cyan' },
  ai_processing: { label: 'AI处理中', color: 'purple' },
  completed: { label: '已完成', color: 'green' },
  failed: { label: '失败', color: 'red' },
  stopped: { label: '已停止', color: 'default' },
};

export function statusMeta(status) {
  return STATUS_META[status] || { label: status || '未知', color: 'default' };
}

export function isActive(status) {
  return ['pending', 'crawling', 'cleaning', 'ai_processing'].includes(status);
}

export function domainOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return url || '';
  }
}

export function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}
