// @ts-check
import react from '@astrojs/react';
import { defineConfig } from 'astro/config';

// The Python editor backend (`mashup serve`). Override when it is on another
// port: MASHUP_API=http://127.0.0.1:9000 pnpm dev
const API_TARGET = process.env.MASHUP_API ?? 'http://127.0.0.1:8765';

export default defineConfig({
  integrations: [react()],
  output: 'static',
  devToolbar: { enabled: false },
  server: { host: '127.0.0.1', port: 4321 },
  vite: {
    server: {
      // In dev the page is served by Astro and the data by Python, so /api is
      // proxied to keep everything same-origin — no CORS, and Range requests
      // for <video> pass straight through. In a production build the Python
      // server serves both from one origin and this proxy is not involved.
      proxy: {
        '/api': {
          target: API_TARGET,
          changeOrigin: false,
        },
      },
    },
  },
});
