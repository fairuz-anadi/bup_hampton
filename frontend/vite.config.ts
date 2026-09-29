import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies /api to the backend so the browser never needs CORS.
const backend = process.env.BACKEND_URL ?? 'http://localhost:8080';

export default defineConfig({
  plugins: [react()],
  // Inline (empty) PostCSS config: stops Vite from picking up a postcss.config.* from a parent directory.
  css: { postcss: {} },
  server: { port: 5173, proxy: { '/api': backend } },
  preview: { port: 5173, proxy: { '/api': backend } },
});
