#!/usr/bin/env node
/**
 * Browser preview of the ported reference UI, driven by the real engine.
 *
 * Electron cannot start a renderer in this environment (docs/08 §4), so this
 * serves the built bundle and provides `window.wmr` through the same
 * `electron/wmrCore.ts` the packaged app uses. Everything below the UI is real:
 * the engine, the protocol, the preview sessions, the annotation sidecar, the
 * pipeline and the upscale. Only the three native bits are substituted:
 *
 *   - the file dialog becomes a prompt
 *   - settings live in memory instead of userData
 *   - "reveal in folder" does nothing
 *
 *   node scripts/dev-preview.mjs [--port 5299]
 */

import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
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
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

const BRIDGE = String.raw`
(() => {
  const METHODS = ${JSON.stringify([
    'select_file', 'select_folder', 'begin_select_file', 'begin_select_folder',
    'poll_dialog_result', 'clear_dialog_result',
    'get_media_info', 'prepare_video_preview',
    'open_video_preview_session', 'read_video_preview_frame', 'close_video_preview_session',
    'load_annotations', 'save_annotations', 'delete_annotations',
    'get_settings', 'save_settings', 'get_device_info',
    'process_video', 'stop_processing', 'open_output_dir',
    'get_model_download_status', 'start_model_download', 'cancel_model_download',
    'get_upscale_model_download_status', 'start_upscale_model_download', 'cancel_upscale_model_download',
    'get_upscale_capabilities', 'start_upscale', 'get_upscale_task_status', 'cancel_upscale_task',
  ])};

  const call = async (method, payload) => {
    const response = await fetch('/wmr', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ method, payload: payload ?? {} }),
    });
    return response.json();
  };

  window.wmr = {};
  for (const method of METHODS) {
    window.wmr[method] = (payload) => call(method, payload);
  }

  // Native dialogs are the one thing a browser cannot provide: a prompt that
  // starts pre-filled keeps the rest of the flow identical.
  const ask = (label, fallback) => {
    const picked = window.prompt(label, fallback);
    return picked === null || picked === '' ? null : picked;
  };
  window.wmr.select_file = async () => {
    const picked = ask('视频文件路径', window.__DEFAULT_VIDEO__);
    return picked ? { path: picked } : null;
  };
  const pending = {};
  window.wmr.begin_select_file = async () => {
    const picked = ask('视频文件路径', window.__DEFAULT_VIDEO__);
    pending.native = { path: picked ?? undefined, cancelled: !picked };
    return { success: true, request_id: 'native' };
  };
  window.wmr.select_folder = async () => {
    const picked = ask('输出目录', window.__OUT_DIR__);
    return picked ? { path: picked } : null;
  };
  window.wmr.begin_select_folder = async () => {
    const picked = ask('输出目录', window.__OUT_DIR__);
    pending.folder = { path: picked ?? undefined, cancelled: !picked };
    return { success: true, request_id: 'folder' };
  };
  window.wmr.poll_dialog_result = async (payload) => {
    const stored = pending[payload?.request_id];
    if (!stored) return { done: true, success: false, error: '对话框已关闭' };
    return { done: true, success: true, cancelled: stored.cancelled, path: stored.path };
  };
  window.wmr.clear_dialog_result = async (payload) => {
    delete pending[payload?.request_id];
    return { success: true };
  };

  // Progress: the harness relays engine notifications as SSE; the UI listens on
  // window with addEventListener, exactly as it does in Electron.
  const source = new EventSource('/events');
  source.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.channel === 'wmr-progress') {
        window.dispatchEvent(new CustomEvent('wmr-progress', { detail: message.payload }));
      }
    } catch (error) { console.error('bridge', error); }
  };

  window.__previewReady = true;
})();
`;

function serveStatic(file, response) {
  const type = MIME[path.extname(file).toLowerCase()] ?? 'application/octet-stream';
  response.writeHead(200, { 'content-type': type });
  response.end(readFileSync(file));
}

async function main() {
  if (!existsSync(path.join(RENDERER, 'index.html'))) {
    throw new Error('renderer bundle missing: npm --prefix desktop run build');
  }

  const { EngineClient } = await import(
    pathToFileURL(path.join(DESKTOP, 'electron', 'services', 'engineClient.ts')).href
  );
  const { wmrEngineCall } = await import(
    pathToFileURL(path.join(DESKTOP, 'electron', 'wmrCore.ts')).href
  );

  const client = new EngineClient({
    command: PYTHON,
    args: ['-u', '-m', 'engine.main'],
    cwd: ROOT,
    onStderr: (line) => process.stderr.write(`[engine] ${line}\n`),
  });

  const sinks = new Set();
  const send = (channel, payload) => {
    const frame = `data: ${JSON.stringify({ channel, payload })}\n\n`;
    for (const sink of sinks) sink.write(frame);
  };

  const capabilities = await client.start();
  console.log(`engine ${capabilities.engine_version} · ${capabilities.platform} · cuda=${capabilities.runtime?.cuda_usable}`);

  let settings = {
    language: 'zh',
    theme: 'dark',
    output: { path: OUT_DIR, model_id: 'lama_roi' },
  };
  let activeTask = null;

  const deps = {
    request: (method, params) => client.request(method, params),
    readSettings: () => settings,
    writeSettings: (patch) => {
      settings = {
        language: patch.language ?? settings.language,
        theme: patch.theme ?? settings.theme,
        output: {
          path: patch.output?.path ?? settings.output.path,
          model_id: patch.output?.model_id ?? settings.output.model_id,
        },
      };
    },
    defaultOutputDir: () => OUT_DIR,
    openPath: async () => undefined,
    pushProgress: (payload) => send('wmr-progress', payload),
    activeTask: {
      get: () => activeTask,
      set: (taskId) => {
        activeTask = taskId;
      },
    },
  };

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
      // Rebuilt on every edit; never let the browser serve a stale bridge.
      response.writeHead(200, { 'content-type': MIME['.js'], 'cache-control': 'no-store' });
      response.end(BRIDGE);
      return;
    }

    if (url.pathname === '/__vars.js') {
      response.writeHead(200, { 'content-type': MIME['.js'], 'cache-control': 'no-store' });
      response.end(`window.__DEFAULT_VIDEO__=${JSON.stringify(FIXTURE)};window.__OUT_DIR__=${JSON.stringify(OUT_DIR)};`);
      return;
    }

    if (url.pathname.startsWith('/media/')) {
      const target = decodeURIComponent(url.pathname.slice('/media/'.length));
      if (!existsSync(target)) { response.writeHead(404).end('not found'); return; }
      const { statSync: stat, createReadStream } = await import('node:fs');
      const info = stat(target);
      const range = request.headers.range;
      const type = target.endsWith('.mp4') ? 'video/mp4' : 'application/octet-stream';
      if (!range) {
        response.writeHead(200, { 'content-type': type, 'content-length': info.size, 'accept-ranges': 'bytes' });
        createReadStream(target).pipe(response);
        return;
      }
      const match = /bytes=(\d*)-(\d*)/.exec(range);
      const start = match && match[1] ? Number(match[1]) : 0;
      const end = match && match[2] ? Number(match[2]) : info.size - 1;
      response.writeHead(206, {
        'content-type': type,
        'content-range': `bytes ${start}-${end}/${info.size}`,
        'content-length': end - start + 1,
        'accept-ranges': 'bytes',
      });
      createReadStream(target, { start, end }).pipe(response);
      return;
    }

    if (url.pathname === '/wmr' && request.method === 'POST') {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString('utf-8') || '{}');
      try {
        const result = await wmrEngineCall(deps, body.method, body.payload ?? {});
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify(result));
      } catch (error) {
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ success: false, error: error?.message ?? String(error) }));
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
    console.log(`preview http://127.0.0.1:${PORT}/`);
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
