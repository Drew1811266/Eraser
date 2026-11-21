#!/usr/bin/env node
/**
 * Drive the assembled desktop app through the whole user flow.
 *
 * This is the real thing: a real Electron window, the real preload bridge, the
 * real IPC handlers, the real engine sidecar and the ported UI. Only the native
 * file dialog is replaced, because no automation can click one.
 *
 * The driving happens *inside* the app (`dist-electron/electron/smoke.js`)
 * rather than through Playwright: Playwright launches Electron with
 * `--inspect=0`, and in this environment the Node inspector takes Chromium down
 * before the first window appears (exit 0x80000003). No debugger, no problem.
 *
 *   node scripts/e2e-desktop.mjs [--keep]
 *
 * Needs an interactive desktop session. Where there is none, use
 * `scripts/e2e-renderer.mjs`: same UI, same engine, browser harness instead.
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DESKTOP = path.join(ROOT, 'desktop');
const CACHE = path.join(ROOT, '.cache');
const FIXTURE = path.join(CACHE, 'fixture.mp4');
const OUT_DIR = path.join(CACHE, 'desktop-out');
const REPORT = path.join(CACHE, 'desktop-smoke.json');
const PYTHON = process.env.ERASER_PYTHON ?? path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const keep = process.argv.includes('--keep');

const ELECTRON = path.join(DESKTOP, 'node_modules', 'electron', 'dist', 'electron.exe');
const ENTRY = path.join(DESKTOP, 'dist-electron', 'electron', 'smoke.js');

async function main() {
  for (const required of [FIXTURE, ENTRY, ELECTRON]) {
    if (!fs.existsSync(required)) {
      throw new Error(`missing ${required} — build the app and generate the fixture first`);
    }
  }
  fs.rmSync(OUT_DIR, { recursive: true, force: true });
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.rmSync(REPORT, { force: true });

  const env = { ...process.env };
  // See scripts/run.mjs: these two kill Chromium before it loads a page.
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.NODE_USE_ENV_PROXY;
  env.ERASER_PYTHON = PYTHON;
  env.ERASER_SMOKE_INPUT = FIXTURE;
  env.ERASER_SMOKE_OUT = REPORT;
  env.ERASER_SMOKE_OUTPUT_DIR = OUT_DIR;

  // A profile inside the workspace: this sandbox cannot write %APPDATA%, and
  // Chromium aborts before the first window when it cannot create its profile.
  const profile = path.join(CACHE, 'desktop-profile');
  fs.rmSync(profile, { recursive: true, force: true });
  // Settings live in the app's profile; seed the output directory so the check
  // knows where to look instead of hunting through the user's Downloads.
  fs.mkdirSync(profile, { recursive: true });
  fs.writeFileSync(
    path.join(profile, 'settings.json'),
    JSON.stringify({ language: 'zh', theme: 'light', output: { path: OUT_DIR, model_id: 'lama_roi' } }, null, 2),
    'utf-8',
  );

  console.log('launching the desktop app');

  const exitCode = await new Promise((resolve) => {
    // `--no-sandbox` is for this harness only: its process token cannot start
    // Chromium's sandbox here, and the app dies before the first window.
    const child = spawn(ELECTRON, ['--no-sandbox', `--user-data-dir=${profile}`, ENTRY], {
      cwd: DESKTOP,
      env,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    const timer = setTimeout(() => {
      console.error('the app did not finish within 10 minutes');
      child.kill();
    }, 10 * 60 * 1000);
    child.on('exit', (code) => {
      clearTimeout(timer);
      resolve(code ?? 1);
    });
  });

  if (!fs.existsSync(REPORT)) {
    console.error(`no smoke report (app exited ${exitCode}) — see .cache/desktop-smoke.json`);
    return 1;
  }

  const report = JSON.parse(fs.readFileSync(REPORT, 'utf-8'));
  for (const entry of report.checks) {
    const status = entry.ok ? 'PASS' : 'FAIL';
    console.log(`  [${status}] ${entry.name}${entry.detail ? ` (${entry.detail})` : ''}`);
  }
  const failed = report.checks.filter((entry) => !entry.ok).length;
  const passed = report.checks.length - failed;

  if (report.error) console.error(`  error: ${report.error}`);
  console.log('');
  console.log(`${passed} passed, ${failed} failed`);
  console.log(`app log: ${report.logPath}`);

  if (!keep) fs.rmSync(OUT_DIR, { recursive: true, force: true });
  return failed === 0 ? 0 : 1;
}

try {
  process.exit(await main());
} catch (error) {
  console.error(error);
  process.exit(1);
}
