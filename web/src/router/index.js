import { createRouter, createWebHistory } from 'vue-router';
import { loadUser } from '../utils/auth';
import { message } from 'ant-design-vue';

const routes = [
  { path: '/', component: () => import('../views/Dashboard.vue') },
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  { path: '/register', name: 'register', component: () => import('../views/Login.vue'), meta: { public: true } },
  { path: '/create', component: () => import('../views/TaskCreator.vue'), meta: { roles: ['admin', 'member'] } },
  { path: '/results/:id', component: () => import('../views/ResultViewer.vue') },
  { path: '/users', component: () => import('../views/Users.vue'), meta: { roles: ['admin'] } },
  { path: '/templates', component: () => import('../views/Templates.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});

router.beforeEach(async (to) => {
  if (to.meta.public) return true;
  try {
    const user = await loadUser();
    if (!user) return { path: '/login', query: { redirect: to.fullPath } };
    if (to.meta.roles && !to.meta.roles.includes(user.role)) {
      message.warning('当前账号没有访问权限');
      return '/';
    }
    return true;
  } catch {
    message.error('无法连接服务，请稍后重试');
    return { path: '/login', query: { redirect: to.fullPath } };
  }
});

export default router;
