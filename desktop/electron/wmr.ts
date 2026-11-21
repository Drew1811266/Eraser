/**
 * Electron host for the `window.wmr` compatibility layer.
 *
 * The reference UI was written against a Python `js_api`: it calls
 * `window.wmr.<method>(payload)`, expects `{ success, ... }` shaped answers, and
 * receives progress as a `wmr-progress` CustomEvent on window. Rewriting 5000
 * lines of Material 3 UI to a different shape would have been a rewrite, not a
 * port — so the shape is provided here instead.
 *
 * This file owns only the parts that need a desktop: dialogs, settings on disk,
 * revealing files, and delivering progress into the page. Everything that talks
 * to the engine lives in `wmrCore.ts` so the browser preview can exercise it too.
 */

import { BrowserWindow, app, dialog, shell } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import type { EngineHost } from './services/engineHost';
import { wmrEngineCall, type WmrDeps, type WmrSettings } from './wmrCore';

const VIDEO_FILTERS = [
  { name: '视频', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'flv', 'wmv', 'm4v'] },
  { name: '所有文件', extensions: ['*'] },
];

const DEFAULT_SETTINGS: WmrSettings = {
  language: 'zh',
  theme: 'dark',
  output: { path: '', model_id: 'lama_roi' },
};

let activeTask: string | null = null;
/**
 * The UI opens a dialog with `begin_*`, then polls `poll_dialog_result` until
 * `done`. Electron's dialog is already modal and awaited, so the selection is
 * recorded here and the very next poll reports it.
 */
const dialogResults = new Map<string, { path?: string; cancelled: boolean }>();
let dialogSeq = 0;
let pendingFlush: NodeJS.Timeout | null = null;
let latestProgress: Record<string, unknown> | null = null;

// -- settings -------------------------------------------------------------
function settingsPath(): string {
  return path.join(app.getPath('userData'), 'settings.json');
}

function readSettings(): WmrSettings {
  try {
    const raw = JSON.parse(fs.readFileSync(settingsPath(), 'utf-8')) as Partial<WmrSettings>;
    return {
      language: raw.language === 'en' ? 'en' : 'zh',
      theme: raw.theme === 'light' ? 'light' : 'dark',
      output: {
        path: raw.output?.path ?? '',
        model_id: raw.output?.model_id ?? DEFAULT_SETTINGS.output.model_id,
      },
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function writeSettings(patch: Partial<WmrSettings>): void {
  const current = readSettings();
  const merged: WmrSettings = {
    language: patch.language === 'en' || patch.language === 'zh' ? patch.language : current.language,
    theme: patch.theme === 'light' || patch.theme === 'dark' ? patch.theme : current.theme,
    output: {
      path: patch.output?.path ?? current.output.path,
      model_id: patch.output?.model_id ?? current.output.model_id,
    },
  };
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(merged, null, 2), 'utf-8');
}

function defaultOutputDir(): string {
  const stored = readSettings().output.path;
  if (stored) return stored;
  return path.join(app.getPath('downloads'), 'Eraser');
}

// -- progress delivery ----------------------------------------------------
/**
 * Push a progress frame into the page.
 *
 * Executed in the page's own world rather than dispatched from the preload: the
 * UI listens with `window.addEventListener`, and this is the delivery path that
 * is guaranteed to run in the world that listener lives in. Throttled, because
 * the engine emits on every processed frame.
 */
export function pushProgress(window: BrowserWindow | null, payload: Record<string, unknown>): void {
  if (!window || window.isDestroyed()) return;
  latestProgress = payload;
  if (pendingFlush) return;

  pendingFlush = setTimeout(() => {
    pendingFlush = null;
    const detail = latestProgress;
    latestProgress = null;
    if (!detail || !window || window.isDestroyed()) return;
    const script = `window.dispatchEvent(new CustomEvent('wmr-progress', { detail: ${JSON.stringify(detail)} }));`;
    window.webContents.executeJavaScript(script, true).catch(() => undefined);
  }, 180);
}

// -- dialogs --------------------------------------------------------------
async function pickPath(window: BrowserWindow | null, kind: 'file' | 'folder'): Promise<string | null> {
  const properties: Array<'openFile' | 'openDirectory' | 'createDirectory'> =
    kind === 'file' ? ['openFile'] : ['openDirectory', 'createDirectory'];
  const options = kind === 'file' ? { properties, filters: VIDEO_FILTERS } : { properties };
  const result = window
    ? await dialog.showOpenDialog(window, options as never)
    : await dialog.showOpenDialog(options as never);
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0] ?? null;
}

// -- router ---------------------------------------------------------------
export async function wmrCall(
  host: EngineHost,
  method: string,
  payload: Record<string, unknown> = {},
  window: BrowserWindow | null = null,
): Promise<unknown> {
  switch (method) {
    case 'select_file':
    case 'begin_select_file':
    case 'select_folder':
    case 'begin_select_folder': {
      const kind = method.includes('folder') ? 'folder' : 'file';
      const picked = await pickPath(window, kind);
      if (method.startsWith('select_')) {
        return picked ? { path: picked } : null;
      }
      const requestId = `native-${(dialogSeq += 1)}`;
      dialogResults.set(requestId, { path: picked ?? undefined, cancelled: !picked });
      if (dialogResults.size > 16) {
        dialogResults.delete(dialogResults.keys().next().value as string);
      }
      return { success: true, request_id: requestId };
    }
    case 'poll_dialog_result': {
      const requestId = String(payload.request_id ?? '');
      const stored = dialogResults.get(requestId);
      if (!stored) return { done: true, success: false, error: '对话框已关闭' };
      return {
        done: true,
        success: true,
        cancelled: stored.cancelled,
        path: stored.path,
      };
    }
    case 'clear_dialog_result':
      dialogResults.delete(String(payload.request_id ?? ''));
      return { success: true };
    default:
      break;
  }

  const deps: WmrDeps = {
    request: (engineMethod, params) => host.request(engineMethod, params),
    readSettings,
    writeSettings,
    defaultOutputDir,
    // shell.openPath resolves with an error string; the dep is void.
    openPath: async (target) => { await shell.openPath(target); },
    pushProgress: (progress) => pushProgress(window, progress),
    activeTask: {
      get: () => activeTask,
      set: (taskId) => {
        activeTask = taskId;
      },
    },
  };
  return wmrEngineCall(deps, method, payload);
}

/** The window to send progress to; set once the main window exists. */
export function attachWindow(window: BrowserWindow): void {
  if (!window.isDestroyed()) {
    pushProgress(window, { progress: 0, status: 'idle', phase: '' });
  }
}
