import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || (import.meta.env.PROD ? '/api/v1' : 'http://localhost:8000/api/v1'),
  timeout: 30000,
});

api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('crawler_access_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use((response) => response, (error) => {
  if (error.response?.status === 401 && error.config?.url !== '/auth/login') {
    sessionStorage.removeItem('crawler_access_token');
    window.dispatchEvent(new Event('auth-expired'));
  }
  return Promise.reject(error);
});

export function errorMessage(error) {
  let data = error?.response?.data;
  if (data instanceof ArrayBuffer) data = new TextDecoder('utf-8').decode(data);
  if (typeof data === 'string') {
    try { data = JSON.parse(data); } catch { /* Use the network error below. */ }
  }
  const detail = data?.detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join('；');
  }
  if (typeof detail === 'string') return detail;
  return error?.message || '请求失败';
}

export default api;
