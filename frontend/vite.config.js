import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// GitHub Pages sets VITE_BASE=/crawler-platform/ . Local and Docker use /.
export default defineConfig({
  plugins: [vue()],
  base: process.env.VITE_BASE || '/',
  server: {
    host: '0.0.0.0',
    port: 3000,
  },
})
