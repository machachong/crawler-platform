import { createRouter, createWebHistory } from 'vue-router';
import Dashboard from '../views/Dashboard.vue';
import TaskCreator from '../views/TaskCreator.vue';
import ResultViewer from '../views/ResultViewer.vue';

const routes = [
  { path: '/', component: Dashboard },
  { path: '/create', component: TaskCreator },
  { path: '/results/:id', component: ResultViewer },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
