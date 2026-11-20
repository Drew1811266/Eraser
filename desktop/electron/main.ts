import { BrowserWindow, app, net, protocol } from 'electron';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { EngineHost, ENGINE_EVENTS } from './services/engineHost';
import { registerIpc } from './ipc';
import { createMainWindow } from './services/window';
import { MEDIA_SCHEME } from '../shared/ipc';

const host = new EngineHost();

// Must happen before `app.whenReady()`: the scheme has to be declared privileged
// early enough for Chromium to treat it as a real, secure origin that can be
// used as a media source.
protocol.registerSchemesAsPrivileged([
  {
    scheme: MEDIA_SCHEME,
    privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
  },
]);

/** Serve exactly one file per request: the one the URL points at. */
export function registerMediaProtocol(): void {
  protocol.handle(MEDIA_SCHEME, async (request) => {
    let relative: string;
    try {
      relative = decodeURIComponent(new URL(request.url).pathname.replace(/^\//, ''));
    } catch {
      return new Response('bad request', { status: 400 });
    }
    if (!relative) {
      return new Response('not found', { status: 404 });
    }
    // `net.fetch` on a file URL honours Range requests, which is what makes
    // seeking in the preview work instead of re-reading the whole file.
    return net.fetch(pathToFileURL(path.resolve(relative)).toString(), { headers: request.headers });
  });
}

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

app.whenReady().then(() => {
  registerMediaProtocol();
  registerIpc(host);
  createMainWindow();

  // Start the engine eagerly: the first probe should not also pay for a cold
  // Python import, and a failure shows up in the UI immediately.
  void host.start().catch(() => undefined);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  void host.stop().finally(() => app.quit());
});

app.on('before-quit', () => {
  void host.stop();
});
