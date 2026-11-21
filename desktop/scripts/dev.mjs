#!/usr/bin/env node
/**
 * Development launcher: Vite dev server + Electron pointed at it.
 *
 * Without this you would run two terminals and remember to export
 * ERASER_DEV_SERVER; the main process already accepts that variable, so the
 * only thing missing was something to start both in the right order.
 *
 *   npm run dev          # hot reload for the UI
 *   npm start            # run the built bundle (no reload, no dev server)
 *
 * Nothing here is needed to *use* the app: `dist/installer/win-unpacked/Eraser.exe`
 * runs without an install, and the installer is only for a real install.
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DESKTOP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const NODE = process.execPath;
const VITE = path.join(DESKTOP, 'node_modules', 'vite', 'bin', 'vite.js');
const ELECTRON = path.join(DESKTOP, 'node_modules', 'electron', 'dist', 'electron.exe');
const DEV_URL = 'http://127.0.0.1:5173';

if (!fs.existsSync(ELECTRON)) {
  console.error('electron is not installed: run `npm install` in desktop/');
  process.exit(1);
}

const children = [];

function start(command, args, options = {}) {
  const child = spawn(command, args, { stdio: 'inherit', ...options });
  children.push(child);
  child.on('exit', (code) => {
    if (shutdownRequested) return;
    console.log(`[dev] ${path.basename(command)} exited (${code})`);
    shutdown();
  });
  return child;
}

let shutdownRequested = false;
function shutdown() {
  if (shutdownRequested) return;
  shutdownRequested = true;
  for (const child of children) {
    if (!child.killed) child.kill();
  }
  setTimeout(() => process.exit(0), 300);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

async function waitForServer(url, timeoutMs = 40000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url, { method: 'GET' });
      if (response.ok) return true;
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

start(NODE, [VITE, '--host', '127.0.0.1', '--port', '5173', '--strictPort'], { cwd: DESKTOP });

if (!(await waitForServer(DEV_URL))) {
  console.error('[dev] vite did not come up; see the output above');
  shutdown();
  process.exit(1);
}

console.log(`[dev] renderer at ${DEV_URL}`);
const electron = start(ELECTRON, ['.'], {
  cwd: DESKTOP,
  env: {
    ...process.env,
    // The host sets this for node-as-electron; Electron itself must not see it.
    ELECTRON_RUN_AS_NODE: undefined,
    ERASER_DEV_SERVER: DEV_URL,
  },
});

if (process.env.ERASER_DEV_PYTHON) {
  electron.on('spawn', () => console.log(`[dev] engine: ${process.env.ERASER_DEV_PYTHON}`));
}
