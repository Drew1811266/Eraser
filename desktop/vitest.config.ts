import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

/**
 * One test runner for both halves of the shell.
 *
 * The renderer tests need jsdom (and say so per file); the main-process tests
 * mock `electron` and run in node. Keeping them in one project means
 * `npm test` covers the IPC contract and the UI together.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    root: __dirname,
    setupFiles: [path.resolve(__dirname, 'renderer', 'test-setup.ts')],
    include: ['renderer/**/*.test.ts', 'renderer/**/*.test.tsx', 'electron/**/*.test.ts'],
    exclude: ['node_modules/**', 'dist-electron/**', 'dist-renderer/**'],
    // The ported reference UI ships its own tests; the hand-written ones covered
    // the shell that replaced it. Do not let an empty selection look green.
    passWithNoTests: false,
  },
});
