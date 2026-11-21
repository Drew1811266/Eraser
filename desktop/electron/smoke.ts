/**
 * In-app smoke driver: the real desktop app, driven from the main process.
 *
 * `e2e-desktop.mjs` normally drives the window with Playwright, but Playwright
 * launches Electron with `--inspect=0`, and in some restricted environments the
 * Node inspector takes Chromium down before the first window (exit 0x80000003).
 * Running the checks from inside the app avoids that: no debugger, no remote
 * port, just `webContents.executeJavaScript` against the same page a user sees.
 *
 *   ERASER_SMOKE_INPUT=clip.mp4 ERASER_SMOKE_OUT=report.json electron dist-electron/electron/smoke.js
 *
 * It reports JSON and exits; the caller decides what a failure means.
 */

import { BrowserWindow, app, dialog } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { EngineHost } from './services/engineHost';
import { registerIpc } from './ipc';
import { createMainWindow } from './services/window';
import { declareMediaScheme, registerMediaProtocol } from './services/media';
import { log, startLog } from './services/log';

const input = process.env.ERASER_SMOKE_INPUT;
const output = process.env.ERASER_SMOKE_OUT;

interface Result {
  ok: boolean;
  checks: Array<{ name: string; ok: boolean; detail?: string }>;
  error?: string;
  logPath: string;
}

const results: Result = { ok: false, checks: [], logPath: '' };

function record(name: string, ok: boolean, detail?: string): void {
  results.checks.push(detail === undefined ? { name, ok } : { name, ok, detail });
}

function finish(ok: boolean, error?: string): void {
  const hold = process.env.ERASER_SMOKE_HOLD === '1';
  results.ok = ok;
  results.logPath = logPathOf();
  if (error) results.error = error;
  if (output) {
    try {
      fs.writeFileSync(output, JSON.stringify(results, null, 2), 'utf-8');
    } catch (writeError) {
      log('cannot write smoke report', writeError);
    }
  }
  if (hold) {
    log('holding the window open for inspection');
    return;
  }
  app.exit(ok ? 0 : 1);
}

function logPathOf(): string {
  try {
    return path.join(app.getPath('userData'), 'logs', 'main.log');
  } catch {
    return '';
  }
}

const host = new EngineHost();

async function click(page: Target, label: string): Promise<string> {
  return page.executeJavaScript(`(() => {
    const hosts = Array.from(document.querySelectorAll('md-filled-button, md-outlined-button, md-text-button, button'));
    const host = hosts.find((entry) => (entry.textContent || entry.getAttribute('aria-label') || '').includes(${JSON.stringify(label)}));
    if (!host) return 'missing';
    if (host.disabled) return 'disabled';
    host.click();
    return 'clicked';
  })()`, true);
}

type Target = Electron.WebContents;

async function waitFor(page: Target, expression: string, timeoutMs: number): Promise<boolean> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = await page.executeJavaScript(`Boolean(${expression})`, true).catch(() => false);
    if (value) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

declareMediaScheme();

app.whenReady().then(async () => {
  startLog();
  log('smoke start', { input, output });

  if (!input) {
    finish(false, 'ERASER_SMOKE_INPUT is not set');
    return;
  }

  // The native dialog cannot be clicked by automation; everything else stays real.
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [input] }) as never;

  registerMediaProtocol();
  log('media protocol registered');
  registerIpc(host);
  log('ipc registered');
  // Same eager start as the real entry point: the first probe should not also
  // pay for a cold Python import.
  void host.start().then(
    () => log('engine started'),
    (error) => log('engine failed to start', error),
  );
  const window = createMainWindow();
  log('window created');
  // A broken preload explains "the bridge is undefined" better than any guess.
  window.webContents.on('preload-error', (_event, preloadPath, error) => {
    log('preload error', { preloadPath, message: error.message, stack: error.stack?.slice(0, 400) });
  });
  window.webContents.on('console-message', (_event, level, message) => {
    if (level >= 2) log('renderer console', message.slice(0, 300));
  });
  const page = window.webContents;

  try {
    const mounted = await waitFor(page, "document.querySelector('.app-nav-rail')", 60000);
    record('renderer mounted', mounted);
    if (!mounted) {
      finish(false, 'the window never rendered');
      return;
    }

    const title = window.getTitle();
    record('window title is Eraser', title === 'Eraser', title);

    const deviceJson = await page
      .executeJavaScript("window.wmr.get_device_info().then((r) => JSON.stringify(r)).catch((e) => 'error: ' + e.message)", true)
      .catch((error: unknown) => `threw: ${String(error)}`);
    record('engine answers through the bridge', !String(deviceJson).startsWith('error')
      && !String(deviceJson).startsWith('threw'), String(deviceJson).slice(0, 90));
    const ready = await waitFor(page, "/NVIDIA|CPU/.test(document.body.textContent || '')", 60000);
    record('engine sidecar is up', ready);

    record('选择视频 clicked', (await click(page, '选择视频')) === 'clicked');
    const imported = await waitFor(
      page,
      "(document.querySelector('.process-toolbar')?.textContent || '').includes('640')",
      60000,
    );
    record('metadata came from the engine', imported);

    // Seed the selection through the same storage call the canvas uses.
    // Synthesised MouseEvents do not satisfy the component's drag state machine
    // (Playwright's trusted input does, and `e2e-renderer.mjs` covers drawing);
    // what matters here is that persistence and processing work inside Electron.
    await page.executeJavaScript(`window.wmr.save_annotations({
      video_path: ${JSON.stringify(input)},
      segments: [{
        start_frame: 0, end_frame: 47,
        rect: { x: 480, y: 20, width: 120, height: 60 },
        expand_px: 5, feather_px: 3, enabled: true,
      }],
    }).then(() => 'saved')`, true);
    record('annotations saved through the bridge', true);

    // Re-import so the app picks the sidecar up, the way a second session would.
    await page.executeJavaScript("Array.from(document.querySelectorAll('.app-nav-destination'))[0].click()", true);
    await new Promise((resolve) => setTimeout(resolve, 500));
    await click(page, '选择视频');
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const segments = await page.executeJavaScript(
      "document.querySelector('.process-right-card')?.innerText?.includes('1') ? 'one' : 'none'",
      true,
    );
    record('the saved annotation is loaded back', segments === 'one', String(segments));

    await page.executeJavaScript("Array.from(document.querySelectorAll('.app-nav-destination'))[0].click()", true);
    await new Promise((resolve) => setTimeout(resolve, 600));
    record('开始处理 clicked', (await click(page, '开始处理')) === 'clicked');

    // ERASER_SMOKE_OUTPUT_DIR is already a directory (the app writes into it).
    const outputDirectory = String(process.env.ERASER_SMOKE_OUTPUT_DIR ?? path.dirname(input));
    const deadline = Date.now() + 5 * 60 * 1000;
    let produced: string[] = [];
    while (Date.now() < deadline) {
      produced = fs.existsSync(outputDirectory)
        ? fs
            .readdirSync(outputDirectory)
            .filter((name) => name.endsWith('_no_watermark.mp4'))
            .filter((name) => {
              try {
                return fs.statSync(path.join(outputDirectory, name)).size > 50_000;
              } catch {
                return false;
              }
            })
        : [];
      if (produced.length > 0) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    record('the pipeline wrote an output file', produced.length > 0, produced[0] ?? 'none');

    await page.executeJavaScript("Array.from(document.querySelectorAll('.app-nav-destination'))[2].click()", true);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const resultText = await page.executeJavaScript(
      "(document.querySelector('.app-main-content')?.innerText || '').slice(0, 400)",
      true,
    );
    record('the result view shows the file', /_no_watermark\.mp4/.test(String(resultText)));

    const failedChecks = results.checks.filter((entry) => !entry.ok).length;
    finish(failedChecks === 0, failedChecks === 0 ? undefined : `${failedChecks} check(s) failed`);
  } catch (error) {
    log('smoke failed', error);
    finish(false, error instanceof Error ? error.message : String(error));
  }
});

app.on('window-all-closed', () => {
  void host.stop().finally(() => app.exit(results.ok ? 0 : 1));
});
