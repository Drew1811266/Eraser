/**
 * Where things live, in development and in a packaged install.
 *
 * The two modes differ in exactly two ways — where the engine executable is and
 * where the renderer's HTML is — so both are resolved here instead of being
 * scattered through the main process. Relative paths are derived from
 * `app.getAppPath()` rather than `__dirname`, because `__dirname` moves with the
 * TypeScript output layout while the app root does not.
 */

import { app } from 'electron';
import fs from 'node:fs';
import path from 'node:path';

export interface EngineLaunch {
  command: string;
  args: string[];
  cwd: string;
}

/** The directory that holds `package.json` — `desktop/` when running from source. */
export function appRoot(): string {
  return app.getAppPath();
}

/** Repository root when running from source. */
export function repoRoot(): string {
  return path.resolve(appRoot(), '..');
}

function isWindows(): boolean {
  return process.platform === 'win32';
}

/**
 * Locate the engine.
 *
 * Packaged builds ship a frozen `eraser-engine.exe` inside `resources/engine`.
 * During development the engine runs from the repository's virtualenv, which
 * must exist — a missing venv is reported as a clear failure rather than a
 * cryptic spawn ENOENT.
 */
export function resolveEngineLaunch(): EngineLaunch {
  if (app.isPackaged) {
    const root = path.join(process.resourcesPath, 'engine');
    const executable = path.join(root, isWindows() ? 'eraser-engine.exe' : 'eraser-engine');
    return { command: executable, args: [], cwd: root };
  }

  const root = repoRoot();
  const candidates = [
    process.env.ERASER_PYTHON,
    path.join(root, '.venv', 'Scripts', 'python.exe'),
    path.join(root, '.venv', 'bin', 'python'),
  ].filter((value): value is string => Boolean(value));

  const command = candidates.find((candidate) => fs.existsSync(candidate)) ?? candidates[0] ?? 'python';
  return { command, args: ['-u', '-m', 'engine.main'], cwd: root };
}

/** The renderer entry the BrowserWindow should load. */
export function rendererEntry(devServerUrl: string | undefined): { kind: 'url' | 'file'; value: string } {
  if (devServerUrl) {
    return { kind: 'url', value: devServerUrl };
  }
  return { kind: 'file', value: path.join(appRoot(), 'dist-renderer', 'index.html') };
}

export function preloadEntry(): string {
  return path.join(__dirname, 'preload.js');
}
