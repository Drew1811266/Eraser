import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

/**
 * The renderer is a plain Vite app rooted at `renderer/`. `base: './'` matters:
 * the packaged window loads `index.html` over `file://`, where absolute asset
 * paths resolve to the filesystem root.
 */
export default defineConfig({
  root: path.resolve(__dirname, 'renderer'),
  base: './',
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, 'dist-renderer'),
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5273,
    strictPort: true,
  },
  test: {
    setupFiles: [path.resolve(__dirname, 'renderer', 'test-setup.ts')],
    include: ['**/*.test.ts', '**/*.test.tsx'],
  },
});
