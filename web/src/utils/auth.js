import { reactive } from 'vue';
import api from './api';

export const auth = reactive({ user: null });
export const roleNames = { admin: '管理员', member: '普通成员', viewer: '只读用户' };
export const canWrite = () => ['admin', 'member'].includes(auth.user?.role);

export function clearAuth() {
  sessionStorage.removeItem('crawler_access_token');
  auth.user = null;
}

export async function loadUser() {
  if (!sessionStorage.getItem('crawler_access_token')) {
    auth.user = null;
    return null;
  }
  try {
    const { data } = await api.get('/auth/me');
    auth.user = data;
    return data;
  } catch (error) {
    if (error.response?.status === 401) {
      clearAuth();
      return null;
    }
    throw error;
  }
}

export async function login(username, password) {
  const { data } = await api.post('/auth/login', { username, password });
  sessionStorage.setItem('crawler_access_token', data.access_token);
  auth.user = data.user;
}

export async function logout() {
  // Keep the session locally if the server cannot revoke it, so logout can be retried.
  try {
    await api.post('/auth/logout');
  } catch (error) {
    if (error.response?.status !== 401) throw error;
  }
  clearAuth();
}
