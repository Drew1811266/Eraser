// @vitest-environment node
/**
 * The preload bridge: what the renderer is actually allowed to touch.
 *
 * Two properties matter and neither is visible from the React side: the exposed
 * object has exactly the shape the renderer's types promise, and every call
 * lands on the same channel the main process registered (see ipc.test.ts).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const invocations: Array<{ channel: string; args: unknown[] }> = [];
const listeners = new Map<string, (event: unknown, payload: unknown) => void>();
let exposed: Record<string, unknown> | null = null;

vi.mock('electron', () => ({
  contextBridge: {
    exposeInMainWorld: (_key: string, value: Record<string, unknown>) => {
      exposed = value;
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

function bridge(): Record<string, (...args: never[]) => unknown> {
  expect(exposed).not.toBeNull();
  return exposed as unknown as Record<string, (...args: never[]) => unknown>;
}

beforeEach(() => {
  invocations.length = 0;
  listeners.clear();
});

describe('preload bridge', () => {
  it('exposes the bridge on the expected global', () => {
    expect(Object.keys(bridge()).sort()).toEqual([
      'cancel', 'capabilities', 'defaultOutputDir', 'onEngineLog', 'onEngineState',
      'onProgress', 'openExternal', 'pickOutputDir', 'pickVideo', 'probe', 'reveal',
      'start', 'status', 'upscale',
    ]);
  });

  it('routes each call to its channel and forwards the payload', async () => {
    const api = bridge();
    await (api.capabilities as () => Promise<unknown>)();
    await (api.probe as (p: string) => Promise<unknown>)('C:/v.mp4');
    await (api.start as (r: unknown) => Promise<unknown>)({ input_path: 'C:/v.mp4', segments: [] });
    await (api.upscale as (r: unknown) => Promise<unknown>)({ input_path: 'C:/v.mp4', scale: 4 });
    await (api.cancel as (id: string) => Promise<unknown>)('task-1');
    await (api.status as (id: string) => Promise<unknown>)('task-1');
    await (api.reveal as (p: string) => Promise<unknown>)('C:/out.mp4');

    expect(invocations.map((entry) => entry.channel)).toEqual([
      IPC.capabilities, IPC.probe, IPC.start, IPC.upscale, IPC.cancel, IPC.status, IPC.reveal,
    ]);
    expect(invocations[1]!.args).toEqual(['C:/v.mp4']);
    expect(invocations[3]!.args).toEqual([{ input_path: 'C:/v.mp4', scale: 4 }]);
  });

  it('subscribes per event and unsubscribes cleanly', () => {
    const api = bridge();
    const seen: unknown[] = [];
    const off = (api.onProgress as (fn: (payload: unknown) => void) => () => void)((payload) =>
      seen.push(payload),
    );

    expect(listeners.has(IPC.progress)).toBe(true);
    const listener = listeners.get(IPC.progress)!;
    listener({}, { progress: 0.5 });
    expect(seen).toEqual([{ progress: 0.5 }]);

    off();
    expect(listeners.has(IPC.progress)).toBe(false);
  });

  it('keeps the three subscriptions independent', () => {
    const api = bridge();
    const progress = vi.fn();
    const state = vi.fn();
    const log = vi.fn();
    (api.onProgress as (fn: unknown) => void)(progress);
    (api.onEngineState as (fn: unknown) => void)(state);
    (api.onEngineLog as (fn: unknown) => void)(log);

    listeners.get(IPC.progress)!({}, { task_id: 't' });
    listeners.get(IPC.engineState)!({}, { status: 'ready' });
    listeners.get(IPC.engineLog)!({}, 'a log line');

    expect(progress).toHaveBeenCalledWith({ task_id: 't' });
    expect(state).toHaveBeenCalledWith({ status: 'ready' });
    expect(log).toHaveBeenCalledWith('a log line');
  });
});
