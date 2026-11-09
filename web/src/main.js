import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import {
  Alert, Button, Card, Checkbox, Col, Collapse, ConfigProvider, Empty, Form, Input, InputNumber,
  Layout, Menu, Modal, PageHeader, Pagination, Popconfirm, Progress, Radio,
  Row, Select, Slider, Space, Spin, Switch, Table, Tag,
} from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import './style.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)
for (const component of [
  Alert, Button, Card, Checkbox, Col, Collapse, ConfigProvider, Empty, Form, Input, InputNumber,
  Layout, Menu, Modal, PageHeader, Pagination, Popconfirm, Progress, Radio,
  Row, Select, Slider, Space, Spin, Switch, Table, Tag,
]) app.use(component)

app.mount('#app')
