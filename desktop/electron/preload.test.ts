// @vitest-environment node
/**
 * The preload bridge: what the renderer is actually allowed to touch.
 *
 * Two globals are exposed. `eraser` is the typed bridge of the Windows shell;
 * `wmr` is the method-per-call shape the ported reference UI was written
 * against. Both must exist, both must route to the same channels the main
 * process registers (see ipc.test.ts), and a rename in one place only is the
 * classic way for this to break at runtime with "Desktop API method not found".
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const invocations: Array<{ channel: string; args: unknown[] }> = [];
const listeners = new Map<string, (event: unknown, payload: unknown) => void>();
const worlds = new Map<string, Record<string, unknown>>();

vi.mock('electron', () => ({
  contextBridge: {
    exposeInMainWorld: (key: string, value: Record<string, unknown>) => {
      worlds.set(key, value);
    },
  },
  ipcRenderer: {
    invoke: async (channel: string, ...args: unknown[]) => {
      invocations.push({ channel, args });
      return { ok: true };
    },
    on: (channel: string, listener: (event: unknown, payload: unknown) => void) => {
      listeners.set(channel, listener);
    },
    removeListener: (channel: string) => {
      listeners.delete(channel);
    },
  },
}));

import { IPC } from '../shared/ipc';

await import('./preload');

function global(name: 'eraser' | 'wmr'): Record<string, (...args: never[]) => unknown> {
  const exposed = worlds.get(name);
  expect(exposed, `${name} is not exposed`).toBeTruthy();
  return exposed as unknown as Record<string, (...args: never[]) => unknown>;
}

beforeEach(() => {
  invocations.length = 0;
  listeners.clear();
});

describe('eraser bridge', () => {
  it('exposes the typed shell surface', () => {
    expect(Object.keys(global('eraser')).sort()).toEqual([
      'cancel', 'capabilities', 'defaultOutputDir', 'onEngineLog', 'onEngineState',
      'onProgress', 'openExternal', 'pickOutputDir', 'pickVideo', 'probe', 'reveal',
      'start', 'status', 'upscale',
    ]);
  });

  it('routes each call to its channel and forwards the payload', async () => {
    const api = global('eraser');
    await (api.capabilities as () => Promise<unknown>)();
    await (api.probe as (p: string) => Promise<unknown>)('C:/v.mp4');
    await (api.start as (r: unknown) => Promise<unknown>)({ input_path: 'C:/v.mp4', segments: [] });
    await (api.cancel as (id: string) => Promise<unknown>)('task-1');

    expect(invocations.map((entry) => entry.channel)).toEqual([
      IPC.capabilities, IPC.probe, IPC.start, IPC.cancel,
    ]);
    expect(invocations[1]!.args).toEqual(['C:/v.mp4']);
  });

  it('subscribes per event and unsubscribes cleanly', () => {
    const api = global('eraser');
    const seen: unknown[] = [];
    const off = (api.onProgress as (fn: (payload: unknown) => void) => () => void)((payload) =>
      seen.push(payload),
    );

    expect(listeners.has(IPC.progress)).toBe(true);
    listeners.get(IPC.progress)!({}, { progress: 0.5 });
    expect(seen).toEqual([{ progress: 0.5 }]);

    off();
    expect(listeners.has(IPC.progress)).toBe(false);
  });
});

describe('wmr bridge', () => {
  it('carries every method the reference UI calls', () => {
    const methods = Object.keys(global('wmr'));
    for (const required of [
      'select_file', 'begin_select_file', 'poll_dialog_result', 'clear_dialog_result',
      'get_media_info', 'prepare_video_preview',
      'open_video_preview_session', 'read_video_preview_frame', 'close_video_preview_session',
      'load_annotations', 'save_annotations', 'delete_annotations',
      'get_settings', 'save_settings', 'get_device_info',
      'process_video', 'stop_processing', 'open_output_dir',
      'get_upscale_capabilities', 'start_upscale', 'get_upscale_task_status', 'cancel_upscale_task',
    ]) {
      expect(methods).toContain(required);
    }
  });

  it('tunnels every call through one channel with its method name', async () => {
    const api = global('wmr');
    await (api.process_video as (p: unknown) => Promise<unknown>)({ input_path: 'C:/v.mp4' });
    await (api.get_settings as () => Promise<unknown>)();

    expect(invocations).toHaveLength(2);
    expect(invocations[0]).toEqual({
      channel: IPC.wmrCall,
      args: [{ method: 'process_video', payload: { input_path: 'C:/v.mp4' } }],
    });
    expect(invocations[1]!.args).toEqual([{ method: 'get_settings', payload: undefined }]);
  });

  it('does not subscribe to anything on its own', () => {
    // Progress reaches the UI as a DOM event; adding an ipcRenderer listener
    // here would duplicate every frame.
    expect(listeners.size).toBe(0);
  });
});
