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

/**
 * The directory that holds `package.json` — `desktop/` when running from source.
 *
 * `getAppPath()` answers that when Electron is given the app directory, but
 * points at the script's own folder when it is given a script (the smoke driver
 * does that). Both are tried, so a launch mode cannot silently move the app root.
 */
export function appRoot(): string {
  const candidates = [app.getAppPath(), path.resolve(__dirname, '..', '..', '..')];
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'package.json'))) {
      return candidate;
    }
  }
  return app.getAppPath();
}

/**
 * Repository root when running from source.
 *
 * Not simply `appRoot()/..`: when Electron is pointed at a script instead of the
 * app directory (the smoke driver does that), `getAppPath()` is the directory of
 * that script, and the engine would be looked for in `dist-electron/.venv`.
 * Walking up from this file's compiled location lands on the repository either
 * way — `desktop/dist-electron/electron` -> `desktop` -> repository.
 */
export function repoRoot(): string {
  let candidate = __dirname;
  for (let depth = 0; depth < 5; depth += 1) {
    if (fs.existsSync(path.join(candidate, 'engine', 'main.py'))) {
      return candidate;
    }
    const parent = path.dirname(candidate);
    if (parent === candidate) break;
    candidate = parent;
  }
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
  // This module lives in `electron/services/`, the preload one level up.
  return path.join(__dirname, '..', 'preload.js');
}
