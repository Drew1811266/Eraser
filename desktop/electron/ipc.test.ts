// @vitest-environment node
/**
 * The IPC layer, pinned.
 *
 * This is the one part of the app that cannot be exercised end to end here
 * (Electron cannot start a renderer in a headless session), so its contract is
 * tested directly: every channel the preload bridge uses must have a handler,
 * and each handler must forward the right engine method with the right payload.
 * A rename in one file and not the other shows up as "引擎无响应" in production,
 * which is a miserable thing to debug from a screenshot.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const handlers = new Map<string, (...args: unknown[]) => unknown>();
const showOpenDialog = vi.fn();
const showItemInFolder = vi.fn();
const openPath = vi.fn();

vi.mock('electron', () => ({
  ipcMain: {
    handle: (channel: string, listener: (...args: unknown[]) => unknown) => {
      handlers.set(channel, listener);
    },
    removeHandler: (channel: string) => handlers.delete(channel),
  },
  dialog: { showOpenDialog: (...args: unknown[]) => showOpenDialog(...args) },
  shell: {
    showItemInFolder: (...args: unknown[]) => showItemInFolder(...args),
    openPath: (...args: unknown[]) => openPath(...args),
  },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
}));

import { registerIpc } from './ipc';
import { EngineHost } from './services/engineHost';
import { IPC } from '../shared/ipc';

function fakeHost(): { host: EngineHost; requests: Array<{ method: string; params: unknown }> } {
  const requests: Array<{ method: string; params: unknown }> = [];
  const host = {
    // `app:capabilities` starts the engine rather than issuing a request, so a
    // caller always gets capabilities for whatever engine ended up running.
    start: async () => ({ engine_version: '0.1.0', devices: [] }),
    request: async (method: string, params: Record<string, unknown>) => {
      requests.push({ method, params });
      if (method === 'media.probe') return { path: params.path, width: 640, height: 360 };
      if (method === 'process.start') return { task_id: 'task-1' };
      if (method === 'process.cancel') return { ok: true };
      if (method === 'process.status') return { task_id: params.task_id, state: 'running' };
      if (method === 'upscale.start') return { task_id: 'task-2' };
      return {};
    },
  } as unknown as EngineHost;
  return { host, requests };
}

beforeEach(() => {
  handlers.clear();
  showOpenDialog.mockReset();
  showItemInFolder.mockReset();
  openPath.mockReset();
});

describe('registerIpc', () => {
  it('starts the engine when capabilities are asked for', async () => {
    const { host } = fakeHost();
    const start = vi.spyOn(host, 'start');
    registerIpc(host);

    const capabilities = await handlers.get(IPC.capabilities)!(null);
    expect(start).toHaveBeenCalled();
    expect(capabilities).toMatchObject({ engine_version: '0.1.0' });
  });

  it('registers a handler for every channel the bridge uses', () => {
    const { host } = fakeHost();
    registerIpc(host);

    const required = [
      IPC.capabilities, IPC.pickVideo, IPC.pickOutputDir, IPC.probe, IPC.start,
      IPC.upscale, IPC.cancel, IPC.status, IPC.reveal, IPC.openExternal, IPC.defaultOutputDir,
    ];
    const missing = required.filter((channel) => !handlers.has(channel));
    expect(missing).toEqual([]);
  });

  it('forwards probe, start, upscale, cancel and status to the right engine methods', async () => {
    const { host, requests } = fakeHost();
    registerIpc(host);
    const call = (channel: string, arg?: unknown) => handlers.get(channel)!(null, arg);

    await call(IPC.probe, 'C:/v.mp4');
    await call(IPC.start, { input_path: 'C:/v.mp4', segments: [] });
    await call(IPC.upscale, { input_path: 'C:/v.mp4', scale: 2 });
    await call(IPC.cancel, 'task-1');
    await call(IPC.status, 'task-1');

    expect(requests.map((entry) => entry.method)).toEqual([
      'media.probe',
      'process.start',
      'upscale.start',
      'process.cancel',
      'process.status',
    ]);
    expect(requests[0]!.params).toEqual({ path: 'C:/v.mp4' });
    expect(requests[3]!.params).toEqual({ task_id: 'task-1' });
  });

  it('returns the engine result unchanged', async () => {
    const { host } = fakeHost();
    registerIpc(host);
    const probe = await handlers.get(IPC.probe)!(null, 'C:/v.mp4');
    expect(probe).toMatchObject({ width: 640, height: 360 });
  });

  it('reports a cancelled dialog instead of an empty path', async () => {
    showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] });
    const { host } = fakeHost();
    registerIpc(host);

    const picked = await handlers.get(IPC.pickVideo)!(null);
    expect(picked).toEqual({ canceled: true });

    showOpenDialog.mockResolvedValue({ canceled: false, filePaths: ['C:/v.mp4'] });
    const second = await handlers.get(IPC.pickVideo)!(null);
    expect(second).toEqual({ canceled: false, path: 'C:/v.mp4' });
  });

  it('reveals files and opens them with the OS', async () => {
    const { host } = fakeHost();
    registerIpc(host);

    await handlers.get(IPC.reveal)!(null, 'C:/out/v.mp4');
    await handlers.get(IPC.openExternal)!(null, 'C:/out/v.mp4');

    expect(showItemInFolder).toHaveBeenCalledWith('C:/out/v.mp4');
    expect(openPath).toHaveBeenCalledWith('C:/out/v.mp4');
  });

  it('surfaces an engine failure as a rejected invoke', async () => {
    const host = {
      request: async () => {
        throw new Error('ENGINE_EXITED: boom');
      },
    } as unknown as EngineHost;
    registerIpc(host);

    await expect(handlers.get(IPC.probe)!(null, 'C:/v.mp4')).rejects.toThrow(/ENGINE_EXITED/);
  });
});

describe('analysis methods', () => {
  it('forwards the analysis surface to the engine', async () => {
    const { host, requests } = fakeHost();
    registerIpc(host);
    const call = (channel: string, arg?: unknown) => handlers.get(channel)!(null, arg);

    await call(IPC.analyzeCapabilities);
    await call(IPC.analyzeStart, { input_path: 'C:/v.mp4', sample_step: 5 });
    await call(IPC.compileAnnotations, {
      watermarks: [],
      shots: [],
      video: { width: 640, height: 360, frame_count: 100 },
    });
    await call(IPC.installDetector, { model_id: 'florence2' });

    expect(requests.map((entry) => entry.method)).toEqual([
      'analyze.capabilities',
      'analyze.start',
      'annotation.compile',
      'model.install',
    ]);
    expect(requests[1]!.params).toEqual({ input_path: 'C:/v.mp4', sample_step: 5 });
    expect(requests[3]!.params).toEqual({ model_id: 'florence2' });
  });

  it('registers the analysis channels', () => {
    const { host } = fakeHost();
    registerIpc(host);
    const required = [
      IPC.analyzeCapabilities,
      IPC.analyzeStart,
      IPC.compileAnnotations,
      IPC.installDetector,
    ];
    expect(required.filter((channel) => !handlers.has(channel))).toEqual([]);
  });
});
