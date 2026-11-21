#!/usr/bin/env node
/**
 * Drive the *ported* UI in a real browser against the real engine.
 *
 * `scripts/e2e-desktop.mjs` is the ideal test but Electron cannot start a
 * renderer in a headless session (docs/08 §4). This harness covers everything
 * except the Electron IPC layer, which is thin and typed and pinned separately
 * by `desktop/electron/ipc.test.ts`:
 *
 *   built bundle → Material UI → window.wmr → real JSON-RPC → real engine → files
 *
 *   node scripts/e2e-renderer.mjs [--keep]
 */

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startPreviewServer } from './lib/preview-server.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DESKTOP = path.join(ROOT, 'desktop');
const CACHE = path.join(ROOT, '.cache');
const FIXTURE = path.join(CACHE, 'fixture.mp4');
const OUT_DIR = path.join(CACHE, 'renderer-out');
const PYTHON = process.env.ERASER_PYTHON ?? path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const keep = process.argv.includes('--keep');

let passed = 0;
let failed = 0;

function check(name, ok, detail = '') {
  if (ok) {
    passed += 1;
    console.log(`  [PASS] ${name}${detail ? ` (${detail})` : ''}`);
  } else {
    failed += 1;
    console.log(`  [FAIL] ${name}${detail ? ` (${detail})` : ''}`);
  }
}

/** Material Web buttons live in shadow DOM; click the host component. */
async function clickByText(page, text) {
  return page.evaluate((label) => {
    const hosts = Array.from(document.querySelectorAll('md-filled-button, md-outlined-button, md-text-button, button'));
    const host = hosts.find((entry) => (entry.textContent || entry.getAttribute('aria-label') || '').includes(label));
    if (!host) return 'missing';
    if (host.disabled) return 'disabled';
    host.click();
    return 'clicked';
  }, text);
}

async function main() {
  fs.rmSync(OUT_DIR, { recursive: true, force: true });

  const server = await startPreviewServer({
    root: ROOT,
    fixture: FIXTURE,
    outputDir: OUT_DIR,
    python: PYTHON,
    log: () => {},
  });
  console.log(`serving ${server.url}`);

  const requireDesktop = createRequire(path.join(DESKTOP, 'package.json'));
  const { chromium } = requireDesktop('playwright');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    // Seeking/aborted media requests are normal while a preview is replaced.
    if (text.includes('ERR_ABORTED') || text.includes('ERR_UNKNOWN_URL_SCHEME')) return;
    pageErrors.push(text);
  });
  page.on('dialog', async (dialog) => {
    await dialog.accept(FIXTURE);
  });

  try {
    console.log('\n1. the shipped bundle boots and reaches the engine');
    await page.goto(server.url, { waitUntil: 'load' });
    await page.waitForSelector('.app-nav-rail', { timeout: 30000 });
    check('renderer mounted', true);
    check('four destinations are present', (await page.locator('.app-nav-destination').count()) === 4);
    const title = (await page.locator('.app-window-title').textContent()) ?? '';
    check('window title is Eraser', title.trim() === 'Eraser', title.trim());

    await page.waitForTimeout(1500);
    const deviceText = (await page.locator('.process-stat, .process-metrics').first().textContent().catch(() => '')) ?? '';

    console.log('\n2. importing a video fills in real metadata');
    check('选择视频 clicked', (await clickByText(page, '选择视频')) === 'clicked');
    await page.waitForFunction(
      () => (document.querySelector('.process-toolbar')?.textContent || '').includes('640'),
      undefined,
      { timeout: 60000 },
    );
    const meta = (await page.locator('.process-toolbar').innerText()) || '';
    check('resolution comes from the engine', /640\s*×\s*360|640x360/.test(meta), meta.slice(0, 40));
    check('frame count comes from the engine', /48/.test(meta));

    console.log('\n3. the annotation workspace shows a real frame');
    await page.locator('.app-nav-destination').nth(1).click();
    await page.waitForSelector('.workspace-stage', { timeout: 30000 });
    await page.waitForFunction(
      () => Boolean(document.querySelector('.workspace-frame')),
      undefined,
      { timeout: 30000 },
    );
    const frameSrc = (await page.locator('.workspace-frame').getAttribute('src')) ?? '';
    check('preview frame is a JPEG data URL', frameSrc.startsWith('data:image/jpeg;base64,'), `${frameSrc.length} chars`);

    console.log('\n4. drawing a region and saving it');
    const stage = page.locator('.workspace-stage').first();
    const box = await stage.boundingBox();
    if (box) {
      const scale = box.width / 640;
      const x = box.x + 480 * scale;
      const y = box.y + 20 * scale;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + 120 * scale, y + 60 * scale, { steps: 10 });
      await page.mouse.up();
      await page.waitForTimeout(600);
    }
    check('a draft rectangle was drawn', Boolean(box), box ? `${Math.round(box.width)}px stage` : 'no stage');
    check('新增标记段 clicked', (await clickByText(page, '新增标记段')) !== 'missing');
    await page.waitForTimeout(600);
    check('保存标注 clicked', (await clickByText(page, '保存标注')) === 'clicked');
    await page.waitForTimeout(1200);

    const sidecar = FIXTURE.replace(/\.mp4$/i, '.annotations.json');
    check('the annotation sidecar exists on disk', fs.existsSync(sidecar), sidecar);
    if (fs.existsSync(sidecar)) {
      const stored = JSON.parse(fs.readFileSync(sidecar, 'utf-8'));
      check('one segment was persisted', Array.isArray(stored.segments) && stored.segments.length >= 1,
        `${stored.segments?.length ?? 0} segment(s)`);
    }

    console.log('\n5. processing runs the real pipeline');
    await page.locator('.app-nav-destination').nth(0).click();
    await page.waitForTimeout(800);
    check('开始处理 clicked', (await clickByText(page, '开始处理')) === 'clicked');
    await page.waitForFunction(
      () => {
        const progress = document.querySelector('md-linear-progress');
        return progress && Number(progress.value) > 0.02;
      },
      undefined,
      { timeout: 120000 },
    );
    check('progress moved past the start', true);

    await page.waitForTimeout(1000);
    await page.waitForFunction(
      () => !document.querySelector('.md-linear-progress') ||
        Number(document.querySelector('md-linear-progress').value) >= 0.99,
      undefined,
      { timeout: 300000 },
    );
    await page.waitForTimeout(1500);

    // Progress reaches 1.0 before the muxer has renamed its temp file, so wait
    // for the artefact rather than sampling the directory once.
    const deadline = Date.now() + 90_000;
    let outputs = [];
    while (Date.now() < deadline) {
      outputs = fs.existsSync(OUT_DIR)
        ? fs.readdirSync(OUT_DIR)
            .filter((name) => name.endsWith('_no_watermark.mp4'))
            .filter((name) => {
              try {
                return fs.statSync(path.join(OUT_DIR, name)).size > 50_000;
              } catch {
                return false;
              }
            })
        : [];
      if (outputs.length > 0) break;
      await page.waitForTimeout(500);
    }
    check('the pipeline wrote an output file', outputs.length === 1, outputs[0] ?? 'none');

    if (outputs.length === 1) {
      const output = path.join(OUT_DIR, outputs[0]);
      const bytes = fs.statSync(output).size;
      check('output is not empty', bytes > 50_000, `${(bytes / 1024).toFixed(0)} KB`);
      check('the engine is still answering afterwards', Boolean(server.capabilities.engine_version));
    }

    console.log('\n6. the result view shows the produced file');
    await page.locator('.app-nav-destination').nth(2).click();
    await page.waitForTimeout(2000);
    const resultText = (await page.locator('.app-main-content').innerText()).slice(0, 300);
    check('result view names the output file', /_no_watermark\.mp4/.test(resultText), resultText.split('\n')[1]?.slice(0, 60));
    check('result view reports the geometry', /640/.test(resultText));

    check('no uncaught page errors', pageErrors.length === 0, pageErrors.slice(0, 2).join(' | '));
  } finally {
    await page.screenshot({ path: path.join(CACHE, 'renderer-shot.png') }).catch(() => {});
    await browser.close();
    await server.close();
    if (!keep) fs.rmSync(OUT_DIR, { recursive: true, force: true });
  }

  console.log('');
  console.log(`${passed} passed, ${failed} failed`);
  return failed === 0 ? 0 : 1;
}

try {
  process.exit(await main());
} catch (error) {
  console.error(error);
  process.exit(1);
}
