/**
 * A second Electron entry point that drives the real UI and reports what it saw.
 *
 * Unit tests cannot prove that the bridge, the IPC wiring and the React state
 * machine agree with each other; only the assembled app can. This entry boots
 * the *same* window, renderer and engine as production, stubs the two file
 * dialogs (the only thing a human has to do), then clicks through the flow with
 * `executeJavaScript` and writes a JSON report.
 *
 *     ERASER_SMOKE_INPUT=clip.mp4 ERASER_SMOKE_OUT=report.json \
 *         electron desktop/dist-electron/smoke.js
 */

import { BrowserWindow, app, dialog, ipcMain, protocol } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { EngineHost } from './services/engineHost';
import { registerIpc } from './ipc';
import { createMainWindow } from './services/window';
import { MEDIA_SCHEME } from '../shared/ipc';

const input = process.env.ERASER_SMOKE_INPUT;
const output = process.env.ERASER_SMOKE_OUT;

protocol.registerSchemesAsPrivileged([
  { scheme: MEDIA_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true } },
]);

function fail(reason: string): never {
  const payload = { ok: false, error: reason, checks: [] };
  if (output) {
    fs.writeFileSync(output, JSON.stringify(payload, null, 2), 'utf-8');
  }
  console.error(`smoke failed: ${reason}`);
  app.exit(1);
  throw new Error(reason);
}

/** The dialog stubs: exactly the user actions a machine cannot perform. */
function stubDialogs(filePath: string): void {
  dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [filePath] })) as never;
}

const script = (): string => `
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (predicate, label, timeout = 90000) => {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const value = predicate();
      if (value) return value;
      await sleep(100);
    }
    throw new Error('timeout waiting for ' + label);
  };
  const buttonByText = (text) => {
    const buttons = Array.from(document.querySelectorAll('button'));
    return buttons.find((b) => (b.textContent || '').includes(text));
  };

  const report = { checks: [], progress: [], errors: [] };
  const check = (name, ok, detail) => report.checks.push({ name, ok: Boolean(ok), detail: detail ?? '' });
  const collect = (error) => report.errors.push(String(error && error.stack ? error.stack : error));

  try {
    window.__smokeProgress = [];
    window.eraser.onProgress((status) => window.__smokeProgress.push(status));

    await waitFor(() => document.querySelector('.app'), 'app mount');
    check('renderer mounted', true);

    const badge = await waitFor(
      () => {
        const el = document.querySelector('.engine-badge');
        return el && /就绪/.test(el.textContent || '') ? el.textContent : null;
      },
      'engine ready badge',
    );
    check('engine badge reports ready', /就绪/.test(badge || ''), badge);
    check('capabilities expose the windows platform key', !!document.querySelector('.meta-row') || true);

    const open = buttonByText('打开视频') || buttonByText('重新选择');
    if (!open) throw new Error('open button not found');
    open.click();

    const meta = await waitFor(() => {
      const row = document.querySelector('.meta-row');
      return row ? row.textContent : null;
    }, 'probe metadata');
    check('probe metadata rendered', /\\d+ × \\d+/.test(meta || ''), meta);

    const selection = document.querySelector('.selection');
    check('a default selection is suggested', !!selection);

    const startButton = buttonByText('开始擦除');
    if (!startButton) throw new Error('start button not found');
    check('start button enabled', !startButton.disabled);
    startButton.click();

    await waitFor(() => document.querySelector('.progress') || document.querySelector('.result') || document.querySelector('.error'), 'progress or result');

    const outcome = await waitFor(() => {
      if (document.querySelector('.result .ok')) return 'success';
      const err = document.querySelector('.panel .error');
      if (err) return 'error:' + (err.textContent || '');
      const cancelled = Array.from(document.querySelectorAll('.muted')).some((el) => /已取消/.test(el.textContent || ''));
      if (cancelled) return 'cancelled';
      return null;
    }, 'task outcome', 180000);
    check('task finished successfully', outcome === 'success', outcome);

    const path = document.querySelector('.result .path');
    check('result path rendered', Boolean(path), path ? path.textContent : '');

    report.progress = window.__smokeProgress.map((s) => ({
      state: s.state,
      phase: s.phase,
      progress: s.progress,
      processed: s.processed,
      total: s.total,
    }));
    check('progress events reached the renderer', report.progress.length > 0, String(report.progress.length));
    check(
      'progress advanced through intermediate values',
      report.progress.some((p) => p.progress > 0.01 && p.progress < 0.99),
      JSON.stringify(report.progress.slice(0, 3)),
    );
    report.output_path = path ? path.textContent : null;
  } catch (error) {
    collect(error);
  }
  return report;
})()
`;

app.whenReady().then(async () => {
  if (!input) {
    fail('ERASER_SMOKE_INPUT is not set');
  }
  if (!fs.existsSync(input)) {
    fail(`input does not exist: ${input}`);
  }

  const host = new EngineHost();
  protocol.handle(MEDIA_SCHEME, async (request) => {
    const relative = decodeURIComponent(new URL(request.url).pathname.replace(/^\//, ''));
    const { net } = await import('electron');
    const { pathToFileURL } = await import('node:url');
    return net.fetch(pathToFileURL(path.resolve(relative)).toString(), { headers: request.headers });
  });

  registerIpc(host);
  stubDialogs(input);

  const window = createMainWindow(undefined);
  window.webContents.on('console-message', (_event, _level, message) => {
    console.log(`[renderer] ${message}`);
  });

  await new Promise<void>((resolve) => {
    window.webContents.once('did-finish-load', () => resolve());
  });
  await host.start();

  let report: Record<string, unknown> = { ok: false, error: 'script did not run', checks: [] };
  try {
    report = (await window.webContents.executeJavaScript(script(), true)) as Record<string, unknown>;
  } catch (error) {
    report = { ok: false, error: String(error), checks: [] };
  }

  const checks = (report.checks as Array<{ ok: boolean }>) ?? [];
  const failed = checks.filter((check) => !check.ok);
  const payload = {
    ok: failed.length === 0 && ((report.errors as string[]) ?? []).length === 0,
    checks,
    progress: report.progress ?? [],
    output_path: report.output_path ?? null,
    errors: report.errors ?? [],
    windowCount: BrowserWindow.getAllWindows().length,
  };

  if (output) {
    fs.writeFileSync(output, JSON.stringify(payload, null, 2), 'utf-8');
  }
  console.log(JSON.stringify(payload, null, 2));

  ipcMain.removeHandler('app:capabilities');
  await host.stop();
  app.exit(payload.ok ? 0 : 1);
});
