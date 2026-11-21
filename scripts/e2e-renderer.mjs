#!/usr/bin/env node
/**
 * Drive the *built* renderer in a real browser against the *real* engine.
 *
 * `scripts/e2e-desktop.mjs` is the ideal test, but Electron cannot start in a
 * headless/CI session (see docs/06 §8): Chromium's renderer fails to initialise
 * even for `about:blank`. This harness covers everything except the Electron
 * IPC layer, which is thin and typed:
 *
 *   Vite bundle → React UI → fetch → real JSON-RPC → real engine → real video
 *
 * The page is served with an injected `window.eraser` shim that speaks to a
 * local /rpc endpoint, so the renderer runs the same code path it would get from
 * preload — same method names, same payloads, same notification stream.
 *
 *   node scripts/e2e-renderer.mjs [--keep] [--skip-cancel]
 */

import { createServer } from 'node:http';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DESKTOP = path.join(ROOT, 'desktop');
const RENDERER = path.join(DESKTOP, 'dist-renderer');
const CACHE = path.join(ROOT, '.cache');
const FIXTURE = path.join(CACHE, 'renderer-fixture.mp4');
const OUT_DIR = path.join(CACHE, 'renderer-out');
const PYTHON = process.env.ERASER_PYTHON ?? path.join(ROOT, '.venv', 'Scripts', 'python.exe');

const keep = process.argv.includes('--keep');
const skipCancel = process.argv.includes('--skip-cancel');

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

// -- the page-side bridge ------------------------------------------------
const BRIDGE = String.raw`
(() => {
  const listeners = { 'task:progress': new Set(), 'engine:state': new Set(), 'engine:log': new Set() };
  const source = new EventSource('/events');
  source.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      const bucket = listeners[message.channel];
      if (bucket) for (const fn of bucket) fn(message.payload);
    } catch (error) {
      console.error('bridge: bad event', error);
    }
  };

  const call = async (method, params) => {
    const response = await fetch('/rpc', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ method, params: params ?? {} }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || ('rpc failed: ' + method));
    return payload.result;
  };

  const subscribe = (channel, fn) => {
    listeners[channel].add(fn);
    return () => listeners[channel].delete(fn);
  };

  window.eraser = {
    capabilities: () => call('system.capabilities'),
    pickVideo: async () => ({ canceled: false, path: window.__FIXTURE__ }),
    pickOutputDir: async () => ({ canceled: false, path: window.__OUT_DIR__ }),
    probe: (p) => call('media.probe', { path: p }),
    start: (request) => call('process.start', request),
    upscale: (request) => call('upscale.start', request),
    cancel: (taskId) => call('process.cancel', { task_id: taskId }),
    status: (taskId) => call('process.status', { task_id: taskId }),
    reveal: async () => undefined,
    openExternal: async () => undefined,
    defaultOutputDir: async () => window.__OUT_DIR__,
    onProgress: (fn) => subscribe('task:progress', fn),
    onEngineState: (fn) => subscribe('engine:state', fn),
    onEngineLog: (fn) => subscribe('engine:log', fn),
  };
})();
`;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

function ensureFixture() {
  if (fs.existsSync(FIXTURE)) {
    return;
  }
  console.log('fixture   building…');
  const result = spawnSync(PYTHON, [
    path.join(ROOT, 'scripts', 'e2e-video.py'), '--fixture-only', '--fixture', FIXTURE,
  ], { encoding: 'utf-8' });
  if (result.status !== 0) {
    throw new Error(`fixture build failed: ${result.stderr}`);
  }
}

function serve(client, notificationSinks) {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');

    if (url.pathname === '/events') {
      response.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        connection: 'keep-alive',
      });
      response.write(': connected\n\n');
      notificationSinks.add(response);
      request.on('close', () => notificationSinks.delete(response));
      return;
    }

    if (url.pathname === '/__vars.js') {
      response.writeHead(200, { 'content-type': MIME['.js'] });
      response.end(
        `window.__FIXTURE__=${JSON.stringify(FIXTURE)};window.__OUT_DIR__=${JSON.stringify(OUT_DIR)};`,
      );
      return;
    }

    if (url.pathname === '/__bridge.js') {
      response.writeHead(200, { 'content-type': MIME['.js'] });
      response.end(BRIDGE);
      return;
    }

    if (url.pathname === '/rpc' && request.method === 'POST') {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      try {
        const body = JSON.parse(Buffer.concat(chunks).toString('utf-8') || '{}');
        const result = await client.request(body.method, body.params ?? {});
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ result }));
      } catch (error) {
        response.writeHead(400, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ error: error?.message ?? String(error) }));
      }
      return;
    }

    // Static files, with the bridge and the fixture injected into index.html.
    const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\//, '');
    const file = path.join(RENDERER, relative);
    if (!file.startsWith(RENDERER) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      response.writeHead(404).end('not found');
      return;
    }

    if (relative === 'index.html') {
      let html = fs.readFileSync(file, 'utf-8');
      // External scripts only: the shipped CSP allows `script-src 'self'`.
      html = html.replace(
        '<script type="module"',
        '<script src="/__vars.js"></script>\n    <script src="/__bridge.js"></script>\n    <script type="module"',
      );
      response.writeHead(200, { 'content-type': MIME['.html'] });
      response.end(html);
      return;
    }

    response.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'application/octet-stream' });
    response.end(fs.readFileSync(file));
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

async function main() {
  if (!fs.existsSync(path.join(RENDERER, 'index.html'))) {
    throw new Error('renderer bundle missing; run: npm --prefix desktop run build');
  }
  ensureFixture();
  fs.rmSync(OUT_DIR, { recursive: true, force: true });
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const { EngineClient } = await import(
    pathToFileURL(path.join(DESKTOP, 'electron', 'services', 'engineClient.ts')).href
  );

  const logs = [];
  const client = new EngineClient({
    command: PYTHON,
    args: ['-u', '-m', 'engine.main'],
    cwd: ROOT,
    onStderr: (line) => logs.push(line),
  });

  const sinks = new Set();
  client.on('notification', (notification) => {
    const frame = `data: ${JSON.stringify({ channel: 'task:progress', payload: notification.params })}\n\n`;
    for (const sink of sinks) sink.write(frame);
  });

  const capabilities = await client.start();
  console.log(`engine    ${capabilities.engine_version} / ${capabilities.platform}`);
  console.log(`runtime   cuda_usable=${capabilities.runtime?.cuda_usable}`);

  const { server, port } = await serve(client, sinks);
  const base = `http://127.0.0.1:${port}/`;
  console.log(`serving   ${base}`);

  // Playwright lives in desktop/node_modules; this script runs from scripts/.
  const requireDesktop = createRequire(path.join(DESKTOP, 'package.json'));
  const { chromium } = requireDesktop('playwright');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });

  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    // `eraser-media:` is a protocol the Electron main process serves; a plain
    // browser has never heard of it, so preview playback cannot work here. The
    // element and its URL are asserted below instead.
    if (text.includes('ERR_UNKNOWN_URL_SCHEME')) return;
    pageErrors.push(text);
  });

  try {
    console.log('\n1. the built bundle boots and reaches the engine');
    await page.goto(base, { waitUntil: 'load' });
    await page.waitForSelector('.app', { timeout: 20000 });
    check('renderer mounted', true);

    await page.waitForSelector('.engine-badge.ready', { timeout: 30000 });
    const badge = (await page.locator('.engine-badge').textContent()) ?? '';
    check('engine badge reports ready', /就绪/.test(badge), badge.trim());
    check('badge names the GPU when usable', /RTX|GPU|CUDA/i.test(badge) || capabilities.runtime?.cuda_usable === false, badge.trim());

    console.log('\n2. picking a video probes it and renders metadata');
    await page.getByRole('button', { name: /打开视频|重新选择/ }).click();
    await page.waitForSelector('.meta-row', { timeout: 20000 });
    const meta = (await page.locator('.meta-row').textContent()) ?? '';
    check('metadata row rendered', /640 × 360/.test(meta), meta.trim().slice(0, 60));
    check('frame count rendered', /48 帧/.test(meta), meta.trim().slice(0, 60));

    const videoSrc = (await page.locator('video.stage-video').getAttribute('src')) ?? '';
    check('preview points at the custom media scheme', videoSrc.startsWith('eraser-media://'), videoSrc.slice(0, 48));

    const rectInputs = page.locator('.panel input[type=number]');
    check('selection is suggested and editable', (await rectInputs.count()) === 4, String(await rectInputs.count()));

    console.log('\n3. processing runs to completion with live progress');
    await page.getByRole('button', { name: '开始擦除' }).click();
    await page.waitForSelector('.progress', { timeout: 20000 });

    let sawIntermediate = false;
    let lastPercent = 0;
    const deadline = Date.now() + 4 * 60 * 1000;
    while (Date.now() < deadline) {
      const done = await page.locator('.result .ok').count();
      if (done) break;
      const error = await page.locator('.panel .error').count();
      if (error) break;
      const percentText = (await page.locator('.progress-head span').nth(1).textContent().catch(() => '0%')) ?? '0%';
      const percent = Number.parseFloat(percentText.replace('%', '')) || 0;
      if (percent > 1 && percent < 99) sawIntermediate = true;
      lastPercent = percent;
      await page.waitForTimeout(250);
    }

    check('progress moved through intermediate values', sawIntermediate, `last ${lastPercent}%`);
    check('task finished', (await page.locator('.result .ok').count()) === 1, 'result panel shown');

    const outputPath = ((await page.locator('.result .path').textContent()) ?? '').trim();
    check('result path rendered', outputPath.endsWith('_no_watermark.mp4'), outputPath);
    check('output file exists on disk', fs.existsSync(outputPath), outputPath);

    if (fs.existsSync(outputPath)) {
      const bytes = fs.statSync(outputPath).size;
      check('output is not empty', bytes > 100_000, `${(bytes / 1024).toFixed(0)} KB`);
      const probe = await client.request('media.probe', { path: outputPath });
      check('output keeps its geometry', probe.width === 640 && probe.height === 360, `${probe.width}x${probe.height}`);
      check('output keeps its frame count', probe.frame_count === 48, String(probe.frame_count));
    }

    console.log('\n4. the result can be upscaled from the same screen');
    await page.getByRole('button', { name: '放大 2×' }).click();
    await page.waitForSelector('.progress', { timeout: 20000 });
    const upscaleDeadline = Date.now() + 4 * 60 * 1000;
    let upscaled = null;
    while (Date.now() < upscaleDeadline) {
      const text = ((await page.locator('.result .path').textContent().catch(() => '')) ?? '').trim();
      if (text.endsWith('_x2.mp4')) {
        upscaled = text;
        break;
      }
      const error = await page.locator('.panel .error').count();
      if (error) break;
      await page.waitForTimeout(300);
    }
    check('upscale produced a file', Boolean(upscaled), upscaled ?? 'none');
    if (upscaled && fs.existsSync(upscaled)) {
      const probe = await client.request('media.probe', { path: upscaled });
      check('upscale doubled the resolution', probe.width === 1280 && probe.height === 720, `${probe.width}x${probe.height}`);
    } else {
      check('upscale doubled the resolution', false, 'file missing');
    }

    if (!skipCancel) {
      console.log('\n5. cancel stops a running job');
      await page.getByRole('button', { name: /打开视频|重新选择/ }).click();
      await page.waitForSelector('.meta-row');
      await page.getByRole('button', { name: '开始擦除' }).click();
      await page.waitForSelector('.progress', { timeout: 20000 });
      await page.getByRole('button', { name: '取消' }).click();
      await page.waitForFunction(
        () => Array.from(document.querySelectorAll('.muted')).some((el) => /已取消/.test(el.textContent || '')),
        undefined,
        { timeout: 120000 },
      );
      check('cancel is reported in the UI', true);
    }

    check('no uncaught page errors', pageErrors.length === 0, pageErrors.slice(0, 2).join(' | '));
  } catch (error) {
    const panelError = await page.locator('.panel .error').first().textContent().catch(() => null);
    const badgeText = await page.locator('.engine-badge').first().textContent().catch(() => null);
    console.error(`\nfailed: ${error.message}`);
    console.error(`  badge: ${badgeText ?? '(none)'}`);
    console.error(`  panel error: ${panelError ?? '(none)'}`);
    for (const entry of pageErrors.slice(0, 5)) console.error(`  page: ${entry}`);
    throw error;
  } finally {
    await page.screenshot({ path: path.join(CACHE, 'renderer-screenshot.png'), fullPage: false }).catch(() => {});
    await browser.close();
    for (const sink of sinks) sink.end();
    server.close();
    await client.shutdown().catch(() => {});
    if (!keep) fs.rmSync(OUT_DIR, { recursive: true, force: true });
  }

  console.log('');
  console.log(`${passed} passed, ${failed} failed`);
  if (failed) {
    console.log('\nengine log (last 25 lines):');
    for (const line of logs.slice(-25)) console.log(`  ${line}`);
  }
  return failed === 0 ? 0 : 1;
}

try {
  process.exit(await main());
} catch (error) {
  console.error(error);
  process.exit(1);
}
