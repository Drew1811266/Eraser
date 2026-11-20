/** Window creation, shared by the real entry point and the smoke test. */

import { BrowserWindow, shell } from 'electron';
import { preloadEntry, rendererEntry } from './paths';

const BACKGROUND = '#0f1115';

export function createMainWindow(devServer: string | undefined = process.env.ERASER_DEV_SERVER): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 840,
    minWidth: 960,
    minHeight: 660,
    backgroundColor: BACKGROUND,
    show: false,
    title: 'Eraser',
    autoHideMenuBar: true,
    webPreferences: {
      preload: preloadEntry(),
      contextIsolation: true,
      nodeIntegration: false,
      // The engine is a plain child process; nothing in the renderer needs Node.
      sandbox: false,
    },
  });

  window.once('ready-to-show', () => window.show());

  // External links belong in the user's browser, never in an app window.
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });

  const entry = rendererEntry(devServer);
  if (entry.kind === 'url') {
    void window.loadURL(entry.value);
  } else {
    void window.loadFile(entry.value);
  }

  return window;
}
