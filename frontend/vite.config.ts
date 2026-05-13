import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('recharts')) return 'finance-viz'
          if (id.includes('lucide-react')) return 'icons'
          if (id.includes('/react/') || id.includes('/react-dom/')) return 'react-vendor'
        },
      },
    },
  },
  server: {
    proxy: {
      '/auth': 'http://localhost:8000',
      '/assistant': 'http://localhost:8000',
      '/artifacts': 'http://localhost:8000',
      '/events': 'http://localhost:8000',
      '/briefings': 'http://localhost:8000',
      '/identity': 'http://localhost:8000',
      '/integrations': 'http://localhost:8000',
      '/connectors': 'http://localhost:8000',
      '/documents': 'http://localhost:8000',
      '/knowledge': 'http://localhost:8000',
      '/health': 'http://localhost:8000'
    }
  }
})
