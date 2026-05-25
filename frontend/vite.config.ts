import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Task 14: outDir flipped from ../backend/static_v2 to ../backend/static (cutover complete).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
        },
      },
    },
  },
  esbuild: {
    drop: ['console', 'debugger'],
  },
  server: {
    port: 5173,
    proxy: {
      '/upload': 'http://localhost:8001',
      '/jobs': 'http://localhost:8001',
      '/preview': 'http://localhost:8001',
      '/healthz': 'http://localhost:8001',
      '/openapi.json': 'http://localhost:8001',
    },
  },
  test: {
    environment: 'happy-dom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/unit/**/*.test.{ts,tsx}'],
    globals: true,
  },
});
