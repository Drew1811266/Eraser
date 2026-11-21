#!/usr/bin/env node
/**
 * Open the ported UI in a browser, backed by the real engine.
 *
 * Electron cannot start a renderer in this environment, so this serves the
 * built bundle and provides `window.wmr` through the same code the packaged app
 * uses. Everything below the UI is real; only the native dialogs, the settings
 * file and "reveal in folder" are substituted.
 *
 *   node scripts/dev-preview.mjs [--port 5299]
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startPreviewServer } from './lib/preview-server.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const portArg = process.argv.indexOf('--port');
const PORT = portArg > -1 ? Number(process.argv[portArg + 1]) : 5299;

const server = await startPreviewServer({
  root: ROOT,
  fixture: path.join(ROOT, '.cache', 'fixture.mp4'),
  outputDir: path.join(ROOT, '.cache', 'preview-out'),
  python: process.env.ERASER_PYTHON ?? path.join(ROOT, '.venv', 'Scripts', 'python.exe'),
  port: PORT,
  log: (line) => process.stderr.write(`${line}\n`),
});

const { capabilities } = server;
console.log(`engine ${capabilities.engine_version} · ${capabilities.platform} · cuda=${capabilities.runtime?.cuda_usable}`);
console.log(`preview ${server.url}`);
console.log(`output  ${path.join(ROOT, '.cache', 'preview-out')}`);

const shutdown = async () => {
  await server.close();
  process.exit(0);
};
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
