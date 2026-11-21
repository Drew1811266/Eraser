#!/usr/bin/env node
/**
 * Launch the built app with Electron.
 *
 * `electron .` works on a normal machine, but it breaks in two situations that
 * are worth handling once: a shell that exports ELECTRON_RUN_AS_NODE (the
 * Electron binary then behaves as plain node, and the app dies with
 * "Cannot read properties of undefined (reading 'registerSchemesAsPrivileged')"),
 * and a missing build. Both are reported here in one line instead of a stack.
 *
 *   npm start      # built bundle, no dev server
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DESKTOP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ELECTRON = path.join(DESKTOP, 'node_modules', 'electron', 'dist', 'electron.exe');
const RENDERER = path.join(DESKTOP, 'dist-renderer', 'index.html');
const MAIN = path.join(DESKTOP, 'dist-electron', 'electron', 'main.js');

if (!fs.existsSync(ELECTRON)) {
  console.error('electron is not installed: run `npm install` in desktop/');
  process.exit(1);
}
if (!fs.existsSync(MAIN) || !fs.existsSync(RENDERER)) {
  console.error('not built yet: run `npm run build` (or `npm run dev` for live reload)');
  process.exit(1);
}

const env = { ...process.env };
// Two variables inherited from a node-shell break Chromium's startup:
//   ELECTRON_RUN_AS_NODE=1  -> the Electron binary behaves as plain node
//   NODE_USE_ENV_PROXY      -> electron's embedded node reconfigures the network
//                              stack and the renderer dies with 0x80000003
//                              before it ever loads a page.
delete env.ELECTRON_RUN_AS_NODE;
delete env.NODE_USE_ENV_PROXY;

const child = spawn(ELECTRON, ['.'], { cwd: DESKTOP, stdio: 'inherit', env });
child.on('exit', (code, signal) => {
  if (signal) {
    console.error(`electron exited on ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 0);
});
