/**
 * The browser harness: the built renderer, the real engine, and the
 * `window.wmr` surface the ported UI expects.
 *
 * Shared by `scripts/dev-preview.mjs` (a human looks at it) and
 * `scripts/e2e-renderer.mjs` (an assertion checks it), so the thing being
 * verified and the thing being demoed cannot drift apart.
 *
 * Electron cannot start a renderer in this environment (docs/08 §4), so this is
 * how the shipped bundle gets exercised: everything below the UI is real. Only
 * three native bits are substituted — the file dialog becomes a prompt, settings
 * live in memory, and "reveal in folder" does nothing.
 */

import { createReadStream, existsSync, mkdirSync, readFileSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.mp4': 'video/mp4',
};

const WMR_METHODS = [
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
];

function bridgeScript({ fixture, outputDir }) {
  return String.raw`
(() => {
  const METHODS = ${JSON.stringify(WMR_METHODS)};

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
  const pending = {};
  window.wmr.select_file = async () => {
    const picked = ask('视频文件路径', ${JSON.stringify(fixture)});
    return picked ? { path: picked } : null;
  };
  window.wmr.begin_select_file = async () => {
    const picked = ask('视频文件路径', ${JSON.stringify(fixture)});
    pending.native = { path: picked ?? undefined, cancelled: !picked };
    return { success: true, request_id: 'native' };
  };
  window.wmr.select_folder = async () => {
    const picked = ask('输出目录', ${JSON.stringify(outputDir)});
    return picked ? { path: picked } : null;
  };
  window.wmr.begin_select_folder = async () => {
    const picked = ask('输出目录', ${JSON.stringify(outputDir)});
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

  // Progress: engine notifications arrive as SSE and are re-dispatched as the
  // DOM event the UI listens for — the same delivery path the app uses.
  const source = new EventSource('/events');
  source.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.channel === 'wmr-progress') {
        window.dispatchEvent(new CustomEvent('wmr-progress', { detail: message.payload }));
      }
    } catch (error) { console.error('bridge', error); }
  };
})();
`;
}

/**
 * Start the harness. Resolves once the engine is up and the port is bound.
 */
export async function startPreviewServer({
  root,
  fixture,
  outputDir,
  python,
  port = 0,
  log = () => {},
}) {
  const desktop = path.join(root, 'desktop');
  const renderer = path.join(desktop, 'dist-renderer');

  if (!existsSync(path.join(renderer, 'index.html'))) {
    throw new Error('renderer bundle missing: npm --prefix desktop run build');
  }
  mkdirSync(outputDir, { recursive: true });

  const { EngineClient } = await import(
    pathToFileURL(path.join(desktop, 'electron', 'services', 'engineClient.ts')).href
  );
  const { wmrEngineCall } = await import(
    pathToFileURL(path.join(desktop, 'electron', 'wmrCore.ts')).href
  );

  const client = new EngineClient({
    command: python,
    args: ['-u', '-m', 'engine.main'],
    cwd: root,
    onStderr: (line) => log(`[engine] ${line}`),
  });
  const capabilities = await client.start();

  const sinks = new Set();
  const send = (channel, payload) => {
    const frame = `data: ${JSON.stringify({ channel, payload })}\n\n`;
    for (const sink of sinks) sink.write(frame);
  };

  let settings = { language: 'zh', theme: 'dark', output: { path: outputDir, model_id: 'lama_roi' } };
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
    defaultOutputDir: () => outputDir,
    openPath: async () => undefined,
    isDirectory: (target) => {
      try {
        return statSync(target).isDirectory();
      } catch {
        return false;
      }
    },
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
      response.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        connection: 'keep-alive',
      });
      response.write(': connected\n\n');
      sinks.add(response);
      request.on('close', () => sinks.delete(response));
      return;
    }

    if (url.pathname === '/__bridge.js') {
      response.writeHead(200, { 'content-type': MIME['.js'], 'cache-control': 'no-store' });
      response.end(bridgeScript({ fixture, outputDir }));
      return;
    }

    if (url.pathname.startsWith('/media/')) {
      const target = decodeURIComponent(url.pathname.slice('/media/'.length));
      if (!existsSync(target)) {
        response.writeHead(404).end('not found');
        return;
      }
      const info = statSync(target);
      const type = MIME[path.extname(target).toLowerCase()] ?? 'application/octet-stream';
      const range = request.headers.range;
      if (!range) {
        response.writeHead(200, {
          'content-type': type,
          'content-length': info.size,
          'accept-ranges': 'bytes',
        });
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
    const file = path.join(renderer, relative);
    if (!file.startsWith(renderer) || !existsSync(file) || statSync(file).isDirectory()) {
      response.writeHead(404).end('not found');
      return;
    }
    if (relative === 'index.html') {
      const html = readFileSync(file, 'utf-8').replace(
        '<script type="module"',
        '<script src="/__bridge.js"></script>\n    <script type="module"',
      );
      response.writeHead(200, { 'content-type': MIME['.html'], 'cache-control': 'no-store' });
      response.end(html);
      return;
    }
    response.writeHead(200, { 'content-type': MIME[path.extname(file).toLowerCase()] ?? 'application/octet-stream' });
    response.end(readFileSync(file));
  });

  await new Promise((resolve) => server.listen(port, '127.0.0.1', resolve));
  const boundPort = server.address().port;

  return {
    port: boundPort,
    url: `http://127.0.0.1:${boundPort}/`,
    capabilities,
    async close() {
      for (const sink of sinks) sink.end();
      server.close();
      await client.shutdown().catch(() => {});
    },
  };
}
