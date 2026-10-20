import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // Codespaces port forwarding sends a *.app.github.dev Host header, which Vite blocks by default.
    allowedHosts: ['.app.github.dev'],
    // The browser only ever talks to this one port; /api/* is forwarded to FastAPI.
    proxy: {
      '/api': { target: 'http://localhost:8000', rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
});
