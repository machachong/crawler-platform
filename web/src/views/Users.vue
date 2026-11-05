<template>
  <div>
    <div class="toolbar"><div><h3>用户管理</h3><p>管理员分配账号与权限，普通成员仅访问自己的任务。</p></div>
      <a-button type="primary" @click="openCreate">新增用户</a-button>
    </div>
    <a-alert v-if="error" :message="error" type="error" show-icon style="margin-bottom: 16px" />
    <a-table :data-source="users" :columns="columns" row-key="id" :loading="loading" :pagination="false" :scroll="{ x: 650 }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'role'">{{ roleNames[record.role] }}</template>
        <template v-else-if="column.key === 'is_active'"><a-tag :color="record.is_active ? 'green' : 'default'">{{ record.is_active ? '启用' : '禁用' }}</a-tag></template>
        <template v-else-if="column.key === 'actions'">
          <a-button type="link" @click="openEdit(record)">编辑权限 / 重置密码</a-button>
        </template>
      </template>
    </a-table>
    <a-space style="margin-top: 16px">
      <a-button :disabled="page === 0 || loading" @click="page--; load()">上一页</a-button>
      <span>第 {{ page + 1 }} 页</span>
      <a-button :disabled="users.length < 50 || loading" @click="page++; load()">下一页</a-button>
    </a-space>
    <a-modal v-model:open="opened" :title="editing ? '编辑用户' : '新增用户'" :footer="null" :mask-closable="false">
      <a-alert v-if="saveError" :message="saveError" type="error" show-icon style="margin-bottom: 16px" />
      <a-form :key="formKey" :model="form" layout="vertical" @finish="save">
        <a-form-item label="账号" name="username" :rules="[{ required: true, pattern: /^[a-zA-Z0-9_.-]{3,64}$/, message: '使用 3–64 位字母、数字、点、下划线或横线' }]">
          <a-input v-model:value="form.username" :disabled="!!editing" autocomplete="off" :maxlength="64" />
        </a-form-item>
        <a-form-item label="角色" name="role">
          <a-select v-model:value="form.role" :disabled="isSelf" :options="roles" />
          <p class="hint">只读用户仅查看、导出归属于自己的任务；管理员可查看和操作全部任务。</p>
        </a-form-item>
        <a-form-item v-if="editing" label="状态"><a-switch v-model:checked="form.is_active" :disabled="isSelf" checked-children="启用" un-checked-children="禁用" /></a-form-item>
        <a-form-item :label="editing ? '重置密码（留空则不修改）' : '初始密码'" name="password"
          :rules="[{ required: !editing, min: 12, max: 128, message: '密码须为 12–128 位' }]">
          <a-input-password v-model:value="form.password" autocomplete="new-password" :maxlength="128" />
        </a-form-item>
        <p class="hint">保存修改会撤销该用户的全部登录会话，用户需重新登录。</p>
        <a-button type="primary" html-type="submit" :loading="saving">保存</a-button>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import api, { errorMessage } from '../utils/api';
import { auth, clearAuth, roleNames } from '../utils/auth';

const router = useRouter();
const users = ref([]), page = ref(0), loading = ref(false), saving = ref(false);
const error = ref(''), saveError = ref(''), opened = ref(false), editing = ref(null), formKey = ref(0);
const form = reactive({ username: '', role: 'member', is_active: true, password: '' });
const roles = Object.entries(roleNames).map(([value, label]) => ({ value, label }));
const isSelf = computed(() => editing.value?.id === auth.user?.id);
const columns = [{ title: '账号', dataIndex: 'username' }, { title: '角色', key: 'role' },
  { title: '状态', key: 'is_active' }, { title: '操作', key: 'actions' }];

async function load() {
  loading.value = true; error.value = '';
  try { users.value = (await api.get('/users', { params: { limit: 50, offset: page.value * 50 } })).data; }
  catch (cause) { error.value = errorMessage(cause); }
  finally { loading.value = false; }
}
function openCreate() {
  editing.value = null;
  Object.assign(form, { username: '', role: 'member', is_active: true, password: '' });
  saveError.value = ''; formKey.value++; opened.value = true;
}
function openEdit(user) {
  editing.value = user;
  Object.assign(form, { username: user.username, role: user.role, is_active: user.is_active, password: '' });
  saveError.value = ''; formKey.value++; opened.value = true;
}
async function save() {
  saving.value = true; saveError.value = '';
  const ownAccount = isSelf.value;
  try {
    if (editing.value) {
      await api.patch(`/users/${editing.value.id}`, {
        role: form.role, is_active: form.is_active, ...(form.password ? { password: form.password } : {}),
      });
    } else {
      await api.post('/users', { username: form.username, role: form.role, password: form.password });
    }
    form.password = ''; opened.value = false; message.success('已保存');
    if (ownAccount) { clearAuth(); await router.replace('/login'); }
    else await load();
  } catch (cause) { saveError.value = errorMessage(cause); }
  finally { saving.value = false; }
}
onMounted(load);
</script>

<style scoped>
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; gap: 16px; }
.toolbar h3 { margin: 0 0 8px; }
.toolbar p, .hint { color: #667085; }
.hint { margin: 8px 0; font-size: 13px; }
</style>
