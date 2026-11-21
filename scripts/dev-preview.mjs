#!/usr/bin/env node
/**
 * Interactive preview of the real UI, without Electron.
 *
 * Electron cannot start a renderer in this environment (see docs/08 §4), so this
 * serves the *built* renderer and wires `window.eraser` to a real engine over
 * its real JSON-RPC. Everything the user clicks is the shipped code path except
 * the two native dialogs, which become prompts, and the `eraser-media:` scheme,
 * which is served over HTTP.
 *
 *   node scripts/dev-preview.mjs [--port 5299]
 *
 * Then open http://127.0.0.1:5299/ in a browser.
 */

import { createServer } from 'node:http';
import { createReadStream, existsSync, statSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DESKTOP = path.join(ROOT, 'desktop');
const RENDERER = path.join(DESKTOP, 'dist-renderer');
const CACHE = path.join(ROOT, '.cache');
const FIXTURE = path.join(CACHE, 'fixture.mp4');
const OUT_DIR = path.join(CACHE, 'preview-out');
const PYTHON = process.env.ERASER_PYTHON ?? path.join(ROOT, '.venv', 'Scripts', 'python.exe');

const portArg = process.argv.indexOf('--port');
const PORT = portArg > -1 ? Number(process.argv[portArg + 1]) : 5299;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.mp4': 'video/mp4',
  '.mov': 'video/quicktime',
  '.mkv': 'video/x-matroska',
};

const BRIDGE = String.raw`
(() => {
  const listeners = { 'task:progress': new Set(), 'engine:state': new Set(), 'engine:log': new Set() };
  const source = new EventSource('/events');
  source.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      const bucket = listeners[message.channel];
      if (bucket) for (const fn of bucket) fn(message.payload);
    } catch (error) { console.error('bridge', error); }
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
    // The native dialogs are the only thing a browser cannot provide; a prompt
    // with the demo path pre-filled keeps the flow identical.
    pickVideo: async () => {
      const picked = window.prompt('视频文件路径', window.__DEFAULT_VIDEO__);
      if (!picked) return { canceled: true };
      return { canceled: false, path: picked };
    },
    pickOutputDir: async () => {
      const picked = window.prompt('输出目录', window.__OUT_DIR__);
      if (!picked) return { canceled: true };
      return { canceled: false, path: picked };
    },
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

  // The app addresses previews with the custom scheme the Electron main process
  // registers; here the same bytes come from /media/.
  const PREFIX = 'eraser-media://local/';
  const fix = (element) => {
    const src = element.getAttribute('src') || '';
    if (src.startsWith(PREFIX)) {
      element.setAttribute('src', '/media/' + src.slice(PREFIX.length));
    }
  };
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === 'attributes' && record.target.tagName === 'VIDEO') fix(record.target);
      for (const node of record.addedNodes || []) {
        if (node.nodeType === 1) {
          if (node.tagName === 'VIDEO') fix(node);
          node.querySelectorAll?.('video').forEach(fix);
        }
      }
    }
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['src'] });
})();
`;

function serveStatic(file, response) {
  const type = MIME[path.extname(file).toLowerCase()] ?? 'application/octet-stream';
  response.writeHead(200, { 'content-type': type });
  response.end(readFileSync(file));
}

function serveMedia(file, request, response) {
  let stat;
  try {
    stat = statSync(file);
  } catch {
    response.writeHead(404).end('not found');
    return;
  }

  const type = MIME[path.extname(file).toLowerCase()] ?? 'video/mp4';
  const range = request.headers.range;
  if (!range) {
    response.writeHead(200, { 'content-type': type, 'content-length': stat.size, 'accept-ranges': 'bytes' });
    createReadStream(file).pipe(response);
    return;
  }

  // Seeking needs byte ranges; a video element asks for them immediately.
  const match = /bytes=(\d*)-(\d*)/.exec(range);
  const start = match && match[1] ? Number(match[1]) : 0;
  const end = match && match[2] ? Number(match[2]) : stat.size - 1;
  if (start >= stat.size || end < start) {
    response.writeHead(416, { 'content-range': `bytes */${stat.size}` }).end();
    return;
  }
  response.writeHead(206, {
    'content-type': type,
    'content-range': `bytes ${start}-${end}/${stat.size}`,
    'content-length': end - start + 1,
    'accept-ranges': 'bytes',
  });
  createReadStream(file, { start, end }).pipe(response);
}

async function main() {
  if (!existsSync(path.join(RENDERER, 'index.html'))) {
    throw new Error('renderer bundle missing: npm --prefix desktop run build');
  }
  if (!existsSync(OUT_DIR)) {
    const { mkdirSync } = await import('node:fs');
    mkdirSync(OUT_DIR, { recursive: true });
  }

  const { EngineClient } = await import(
    pathToFileURL(path.join(DESKTOP, 'electron', 'services', 'engineClient.ts')).href
  );
  const client = new EngineClient({
    command: PYTHON,
    args: ['-u', '-m', 'engine.main'],
    cwd: ROOT,
    onStderr: (line) => process.stderr.write(`[engine] ${line}\n`),
  });

  const sinks = new Set();
  client.on('notification', (notification) => {
    const frame = `data: ${JSON.stringify({ channel: 'task:progress', payload: notification.params })}\n\n`;
    for (const sink of sinks) sink.write(frame);
  });

  const capabilities = await client.start();
  console.log(`engine ${capabilities.engine_version} · ${capabilities.platform} · cuda=${capabilities.runtime?.cuda_usable}`);

  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');

    if (url.pathname === '/events') {
      response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
      response.write(': connected\n\n');
      sinks.add(response);
      request.on('close', () => sinks.delete(response));
      return;
    }

    if (url.pathname === '/__bridge.js') {
      response.writeHead(200, { 'content-type': MIME['.js'] });
      response.end(BRIDGE);
      return;
    }

    if (url.pathname === '/__vars.js') {
      response.writeHead(200, { 'content-type': MIME['.js'] });
      response.end(
        `window.__DEFAULT_VIDEO__=${JSON.stringify(FIXTURE)};window.__OUT_DIR__=${JSON.stringify(OUT_DIR)};`,
      );
      return;
    }

    if (url.pathname.startsWith('/media/')) {
      const file = path.resolve(decodeURIComponent(url.pathname.slice('/media/'.length)));
      serveMedia(file, request, response);
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

    const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\//, '');
    const file = path.join(RENDERER, relative);
    if (!file.startsWith(RENDERER) || !existsSync(file) || statSync(file).isDirectory()) {
      response.writeHead(404).end('not found');
      return;
    }
    if (relative === 'index.html') {
      const html = readFileSync(file, 'utf-8').replace(
        '<script type="module"',
        '<script src="/__vars.js"></script>\n    <script src="/__bridge.js"></script>\n    <script type="module"',
      );
      response.writeHead(200, { 'content-type': MIME['.html'] });
      response.end(html);
      return;
    }
    serveStatic(file, response);
  });

  server.listen(PORT, '127.0.0.1', () => {
    console.log(`preview ${'http://127.0.0.1:' + PORT + '/'}`);
    console.log(`output  ${OUT_DIR}`);
  });

  const shutdown = async () => {
    server.close();
    await client.shutdown().catch(() => {});
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
