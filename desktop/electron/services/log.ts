/**
 * A log file for the main process.
 *
 * A packaged Electron app has no console: `console.log` goes nowhere a user can
 * find, and a crash before the window appears leaves no trace at all. Startup
 * phases and native crash reports are written here so "it closes immediately"
 * becomes a readable line instead of a guess.
 *
 * One file per launch, truncated at start: the interesting half is always the
 * current run.
 */

import { app } from 'electron';
import fs from 'node:fs';
import path from 'node:path';

let target: string | null = null;

function timestamp(): string {
  const now = new Date();
  return now.toISOString().slice(11, 23);
}

function ensureTarget(): string | null {
  if (target) return target;
  try {
    const directory = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(directory, { recursive: true });
    target = path.join(directory, 'main.log');
    return target;
  } catch {
    return null;
  }
}

export function log(message: string, detail?: unknown): void {
  const file = ensureTarget();
  if (!file) return;
  const line =
    detail === undefined
      ? `[${timestamp()}] ${message}`
      : `[${timestamp()}] ${message} ${safeDetail(detail)}`;
  try {
    fs.appendFileSync(file, `${line}\n`, 'utf-8');
  } catch {
    // Logging must never be the reason something fails.
  }
}

function safeDetail(detail: unknown): string {
  if (detail instanceof Error) return `${detail.name}: ${detail.message}`;
  if (typeof detail === 'string') return detail;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

/** Where the current log lives, for the UI and for support. */
export function logPath(): string {
  return ensureTarget() ?? '';
}

/** Start a fresh log and record the facts we always want first. */
export function startLog(): void {
  const file = ensureTarget();
  if (file) {
    try {
      fs.writeFileSync(file, '', 'utf-8');
    } catch {
      // Keep going: an unwritable log is not fatal.
    }
  }
  log('main start', {
    version: app.getVersion(),
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    platform: process.platform,
    arch: process.arch,
    packaged: app.isPackaged,
  });
}
