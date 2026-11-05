import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Production is hosted at /crawler/; VITE_BASE can override it. Local dev uses /.
export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  base: process.env.VITE_BASE || (mode === 'production' ? '/crawler/' : '/'),
  server: {
    host: '0.0.0.0',
    port: 3000,
  },
}))
