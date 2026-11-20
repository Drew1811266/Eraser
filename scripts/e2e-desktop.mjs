#!/usr/bin/env node
/**
 * Drives the assembled desktop app through the real user flow.
 *
 * The engine test proves the pipeline; this proves the *app*: the preload
 * bridge, the IPC handlers, the React state machine and the progress plumbing
 * all have to agree before a single check passes.
 *
 *   node scripts/e2e-desktop.mjs [--keep]
 */

import { spawn, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DESKTOP = path.join(ROOT, 'desktop');
const CACHE = path.join(ROOT, '.cache');
const FIXTURE = path.join(CACHE, 'fixture.mp4');
const REPORT = path.join(CACHE, 'desktop-smoke.json');
const PYTHON = process.env.ERASER_PYTHON ?? path.join(ROOT, '.venv', 'Scripts', 'python.exe');

const keep = process.argv.includes('--keep');

function log(message) {
  process.stdout.write(`${message}\n`);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf-8', ...options });
  if (result.status !== 0) {
    throw new Error(`${command} exited ${result.status}\n${result.stdout ?? ''}\n${result.stderr ?? ''}`);
  }
  return result;
}

function runShell(command, cwd) {
  const result = spawnSync(command, { cwd, shell: true, encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`${command} exited ${result.status}\n${result.stdout ?? ''}\n${result.stderr ?? ''}`);
  }
  return result;
}

function ensureFixture() {
  if (fs.existsSync(FIXTURE)) {
    log(`fixture   ${FIXTURE}`);
    return;
  }
  log('fixture   building…');
  run(PYTHON, [path.join(ROOT, 'scripts', 'e2e-video.py'), '--fixture-only', '--fixture', FIXTURE]);
  log(`fixture   ${FIXTURE}`);
}

/**
 * The host sets ELECTRON_RUN_AS_NODE for its own tooling, which turns Electron
 * into plain Node — no app, no protocol, no BrowserWindow. Delete the key; an
 * empty value still counts as set.
 */
function childEnv() {
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  env.ERASER_SMOKE_INPUT = FIXTURE;
  env.ERASER_SMOKE_OUT = REPORT;
  env.ERASER_PYTHON = PYTHON;
  env.ELECTRON_DISABLE_SECURITY_WARNINGS = '1';
  return env;
}

function electronBinary() {
  const require = createRequire(path.join(DESKTOP, 'package.json'));
  const resolved = require('electron');
  return typeof resolved === 'string' ? resolved : null;
}

function main() {
  fs.mkdirSync(CACHE, { recursive: true });
  ensureFixture();

  log('build     transpiling');
  // One shell string: `npm` is a .cmd on Windows and spawning the wrapper
  // directly fails with a null status.
  runShell('npm run build', DESKTOP);

  const binary = electronBinary();
  if (!binary || !fs.existsSync(binary)) {
    throw new Error('electron binary not found; run npm install in desktop/');
  }

  fs.rmSync(REPORT, { force: true });
  const entry = path.join(DESKTOP, 'dist-electron', 'electron', 'smoke.js');

  log('launch    electron');
  // --no-sandbox is needed in restricted/CI sessions; it only affects the test run.
  const child = spawn(binary, ['--no-sandbox', entry], {
    cwd: DESKTOP,
    env: childEnv(),
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => {
    stdout += chunk.toString();
  });
  child.stderr.on('data', (chunk) => {
    stderr += chunk.toString();
  });

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('smoke test timed out after 10 minutes'));
    }, 10 * 60 * 1000);

    child.on('exit', (code) => {
      clearTimeout(timer);
      try {
        const report = JSON.parse(fs.readFileSync(REPORT, 'utf-8'));
        printReport(report);
        resolve(report.ok ? 0 : 1);
      } catch (error) {
        process.stderr.write(stdout.slice(-4000));
        process.stderr.write(stderr.slice(-8000));
        reject(new Error(`smoke report missing (exit ${code}): ${error.message}`));
      }
    });
  });
}

function printReport(report) {
  for (const check of report.checks ?? []) {
    const status = check.ok ? 'PASS' : 'FAIL';
    const detail = check.detail ? ` (${String(check.detail).slice(0, 120)})` : '';
    log(`  [${status}] ${check.name}${detail}`);
  }
  const passed = (report.checks ?? []).filter((check) => check.ok).length;
  const total = (report.checks ?? []).length;
  log('');
  log(`  progress events: ${(report.progress ?? []).length}`);
  if (report.output_path) {
    log(`  output: ${report.output_path}`);
  }
  for (const error of report.errors ?? []) {
    log(`  error: ${String(error).split('\n')[0]}`);
  }
  log('');
  log(`${passed}/${total} checks passed`);
  if (!keep && report.output_path) {
    try {
      fs.rmSync(path.dirname(report.output_path), { recursive: true, force: true });
    } catch {
      // best effort
    }
  }
}

try {
  const code = await main();
  process.exit(code);
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
}
