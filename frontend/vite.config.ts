import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// The UI is served by the FastAPI process from the packaged web assets, so every API call
// is a same-origin relative path and no dev proxy is required.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/tokenhub/web',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    environmentOptions: {
      // Mirror the loopback origin the local server actually serves on.
      jsdom: { url: 'http://127.0.0.1:7432/' },
    },
  },
});
