import { BrowserWindow, app } from 'electron';
import { EngineHost, ENGINE_EVENTS } from './services/engineHost';
import { registerIpc } from './ipc';
import { createMainWindow } from './services/window';
import { declareMediaScheme, registerMediaProtocol } from './services/media';
import { log, startLog } from './services/log';

const host = new EngineHost();

// Must happen before `app.whenReady()`.
declareMediaScheme();

function broadcast(channel: string, payload: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send(channel, payload);
    }
  }
}

host.on(ENGINE_EVENTS.state, (state) => broadcast('engine:state', state));
host.on(ENGINE_EVENTS.progress, (status) => broadcast('task:progress', status));
host.on(ENGINE_EVENTS.log, (line) => broadcast('engine:log', line));

// Developer/test hook: point the profile somewhere writable. Chromium aborts
// before the first window when it cannot create its profile, which looks like a
// crash in this app rather than a permissions problem.
if (process.env.ERASER_USER_DATA_DIR) {
  app.setPath('userData', process.env.ERASER_USER_DATA_DIR);
}

startLog();

// A crash in a child process (the renderer, or the engine's helpers) would
// otherwise only show up as a window that never fills in.
app.on('render-process-gone', (_event, _contents, details) => {
  log('renderer gone', details);
});
app.on('child-process-gone', (_event, details) => {
  log('child process gone', details);
});
process.on('uncaughtException', (error) => {
  log('uncaught exception', error);
});
process.on('unhandledRejection', (reason) => {
  log('unhandled rejection', reason);
});

app.whenReady().then(() => {
  log('app ready');
  registerMediaProtocol();
  log('media protocol registered');
  registerIpc(host);
  log('ipc registered');
  createMainWindow();
  log('main window created');

  // Start the engine eagerly: the first probe should not also pay for a cold
  // Python import, and a failure shows up in the UI immediately.
  void host.start().then(
    () => log('engine started'),
    (error) => log('engine failed to start', error),
  );

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  log('all windows closed');
  void host.stop().finally(() => app.quit());
});

app.on('before-quit', () => {
  void host.stop();
});
