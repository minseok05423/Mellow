import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two pages: the notebook app, and the M1 ε–δ demo kept runnable until it becomes a widget (P3).
      input: { main: 'index.html', epsdelta: 'epsdelta.html' },
    },
  },
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
