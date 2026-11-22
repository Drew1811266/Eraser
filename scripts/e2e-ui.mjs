#!/usr/bin/env node
/**
 * Drive the real Electron app once: import a clip, analyse it, and confirm the
 * analysis view draws what jsdom cannot — a real video frame, real blocks, a
 * moving playhead.
 *
 *   node scripts/e2e-ui.mjs [--video <path>]
 *
 * Playwright's Electron launcher does not work with this Electron build (it
 * spawns the binary in node mode, which rejects --remote-debugging-port), so the
 * app is started normally with a debug port and driven over CDP. The file dialog
 * is replaced inside the page instead of in the main process.
 */

import { execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DESKTOP = path.join(ROOT, 'desktop');
const OUT = path.join(ROOT, '.cache', 'e2e-ui');
const SHOTS = path.join(OUT, 'shots');
const PORT = 9222;

const require = createRequire(path.join(DESKTOP, 'package.json'));
const { chromium } = require('playwright');

const argVideo = process.argv.indexOf('--video');
const VIDEO = argVideo > 0
  ? path.resolve(process.argv[argVideo + 1])
  : path.join(ROOT, '.cache', 'analysis', 'corner_static', 'video.mp4');

fs.mkdirSync(SHOTS, { recursive: true });

function log(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? `  [${detail}]` : ''}`);
}
const results = [];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForDevtools(timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (response.ok) {
        return await response.json();
      }
    } catch {
      // not up yet
    }
    await sleep(300);
  }
  throw new Error(`devtools endpoint never came up on ${PORT}`);
}

async function main() {
  if (!fs.existsSync(VIDEO)) {
    console.error(`fixture missing: ${VIDEO}\n  run scripts/make-analysis-fixtures.py first`);
    process.exit(2);
  }

  const env = { ...process.env };
  // Two variables from a node shell break Electron: one turns the binary into
  // plain node, the other reconfigures Chromium's network stack.
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.NODE_USE_ENV_PROXY;
  env.ERASER_USER_DATA_DIR = ROOT;
  env.ERASER_TEMP_DIR = path.join(ROOT, '.cache', 'tmp');
  env.ERASER_DEBUG_PORT = String(PORT);

  const child = spawn(
    path.join(DESKTOP, 'node_modules', 'electron', 'dist', 'electron.exe'),
    [path.join(DESKTOP, 'dist-electron', 'electron', 'main.js')],
    { cwd: DESKTOP, env, stdio: 'pipe' },
  );
  child.stdout.on('data', () => {});
  child.stderr.on('data', (chunk) => {
    const text = String(chunk).trim();
    if (text) console.log(`  [main] ${text.slice(0, 160)}`);
  });

  let browser;
  try {
    const version = await waitForDevtools();
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`);
    const context = browser.contexts()[0];
    const page = context.pages()[0] ?? (await context.waitForEvent('page'));
    await page.waitForLoadState('domcontentloaded');

    const shot = async (name) => {
      await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
    };
    await shot('01-start');

    // 用页面内的桩替换两步式文件对话框，避免弹原生窗口。
    await page.evaluate((videoPath) => {
      const wmr = window.wmr;
      wmr.begin_select_file = async () => ({ success: true, request_id: 'e2e' });
      wmr.poll_dialog_result = async () => ({ path: videoPath, canceled: false });
      wmr.begin_select_folder = async () => ({ success: true, request_id: 'e2e' });
    }, VIDEO);

    const pick = page.getByText('选择视频').first();
    await pick.waitFor({ timeout: 30_000 });
    await pick.click();

    const entry = page.getByText('分析水印').first();
    await entry.waitFor({ timeout: 90_000 });
    log('the app imported the clip', true, path.basename(VIDEO));
    await shot('02-imported');

    const started = Date.now();
    await entry.click();

    await page.getByText('分析结果').first().waitFor({ timeout: 240_000 });
    const analysisMs = Date.now() - started;
    log('analysis produced a report', true, `${(analysisMs / 1000).toFixed(1)}s`);
    await shot('03-analysis');

    const blocks = await page.locator('[data-testid^="block-"]').count();
    log('the timeline drew blocks', blocks > 0, `${blocks} block(s)`);
    const lanes = await page.locator('[data-testid^="lane-"]').count();
    log('the timeline drew lanes', lanes > 0, `${lanes} lane(s)`);

    const box = await page.locator('[data-testid^="block-"]').first().boundingBox();
    log(
      'a block has a real rectangle',
      Boolean(box && box.width > 0 && box.width < 600),
      box ? `${Math.round(box.width)}x${Math.round(box.height)}` : 'no box',
    );

    const stage = page.locator('.analysis-stage img');
    const frames = await stage.count();
    log('the stage shows a video frame', frames > 0);

    const before = frames ? await stage.first().getAttribute('src') : null;
    await page.locator('input[type="range"]').fill('50');
    await page.waitForTimeout(1500);
    const after = frames ? await stage.first().getAttribute('src') : null;
    log(
      'moving the playhead changes the frame',
      Boolean(before && after && before !== after),
      before ? `${before.slice(0, 24)} -> ${String(after).slice(0, 24)}` : 'no src',
    );
    await shot('04-seek');

    await page.locator('[data-testid^="block-"]').first().click();
    const nudge = await page.getByLabel('终点后移一帧').count();
    log('selecting a block shows the nudge controls', nudge > 0);
    await shot('05-selected');

    const start = page.getByText('开始处理').first();
    log('processing can be started from the analysis view', await start.isEnabled());

    fs.writeFileSync(
      path.join(OUT, 'results.json'),
      JSON.stringify({ video: VIDEO, analysisMs, results }, null, 2),
      'utf8',
    );
  } finally {
    if (browser) await browser.close().catch(() => {});
    try {
      execFileSync('taskkill', ['/F', '/T', '/PID', String(child.pid)], { stdio: 'ignore' });
    } catch {
      // already gone
    }
  }

  const failed = results.filter((item) => !item.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  console.log(`screenshots: ${SHOTS}`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
