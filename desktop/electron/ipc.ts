/** IPC surface. Every handler is a thin adapter over EngineHost. */

import { BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { EngineError } from './services/engineClient';
import { EngineHost } from './services/engineHost';
import { wmrCall } from './wmr';
import { IPC, type PickResult, type StartRequest, type UpscaleRequest } from '../shared/ipc';

const VIDEO_FILTERS = [
  { name: '视频', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'flv', 'wmv'] },
  { name: '所有文件', extensions: ['*'] },
];

function focusedWindow(): BrowserWindow | null {
  return BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0] ?? null;
}

/** Rethrow engine errors with their code intact so the renderer can branch. */
function rethrow(error: unknown): never {
  if (error instanceof EngineError) {
    throw new Error(`${error.code}: ${error.message}`);
  }
  throw error instanceof Error ? error : new Error(String(error));
}

export function registerIpc(host: EngineHost): void {
  ipcMain.handle(IPC.capabilities, async () => {
    try {
      return await host.start();
    } catch (error) {
      rethrow(error);
    }
  });

  ipcMain.handle(IPC.defaultOutputDir, async () => {
    const { app } = await import('electron');
    const path = await import('node:path');
    return path.join(app.getPath('downloads'), 'Eraser');
  });

  ipcMain.handle(IPC.pickVideo, async (): Promise<PickResult> => {
    const window = focusedWindow();
    const result = window
      ? await dialog.showOpenDialog(window, { properties: ['openFile'], filters: VIDEO_FILTERS })
      : await dialog.showOpenDialog({ properties: ['openFile'], filters: VIDEO_FILTERS });
    if (result.canceled || result.filePaths.length === 0) {
      return { canceled: true };
    }
    return { canceled: false, path: result.filePaths[0] };
  });

  ipcMain.handle(IPC.pickOutputDir, async (): Promise<PickResult> => {
    const window = focusedWindow();
    const options = { properties: ['openDirectory', 'createDirectory'] as const };
    const result = window
      ? await dialog.showOpenDialog(window, { ...options, properties: [...options.properties] })
      : await dialog.showOpenDialog({ ...options, properties: [...options.properties] });
    if (result.canceled || result.filePaths.length === 0) {
      return { canceled: true };
    }
    return { canceled: false, path: result.filePaths[0] };
  });

  ipcMain.handle(IPC.probe, async (_event, filePath: string) => {
    try {
      return await host.request('media.probe', { path: filePath });
    } catch (error) {
      rethrow(error);
    }
  });

  ipcMain.handle(IPC.start, async (_event, request: StartRequest) => {
    try {
      return await host.request('process.start', request as unknown as Record<string, unknown>);
    } catch (error) {
      rethrow(error);
    }
  });

  ipcMain.handle(IPC.upscale, async (_event, request: UpscaleRequest) => {
    try {
      return await host.request('upscale.start', request as unknown as Record<string, unknown>);
    } catch (error) {
      rethrow(error);
    }
  });

  ipcMain.handle(IPC.cancel, async (_event, taskId: string) => {
    try {
      return await host.request('process.cancel', { task_id: taskId });
    } catch (error) {
      rethrow(error);
    }
  });

  ipcMain.handle(IPC.status, async (_event, taskId: string) => {
    try {
      return await host.request('process.status', { task_id: taskId });
    } catch (error) {
      rethrow(error);
    }
  });

  ipcMain.handle(
    IPC.wmrCall,
    async (event, request: { method: string; payload?: Record<string, unknown> }) => {
      const window = BrowserWindow.fromWebContents(event.sender);
      try {
        return await wmrCall(host, request.method, request.payload ?? {}, window);
      } catch (error) {
        // The reference UI renders `error` strings, so a failure is a value.
        return { success: false, error: error instanceof Error ? error.message : String(error) };
      }
    },
  );

  ipcMain.handle(IPC.reveal, async (_event, target: string) => {
    shell.showItemInFolder(target);
  });

  ipcMain.handle(IPC.openExternal, async (_event, target: string) => {
    await shell.openPath(target);
  });
}
