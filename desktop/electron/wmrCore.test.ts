// @vitest-environment node
/**
 * The compatibility layer, tested where it cannot hide.
 *
 * The ported UI talks to `window.wmr`, and everything it sees passes through
 * `wmrEngineCall`. These tests pin the translation in both directions: what the
 * engine returns becomes the shape the UI renders, and what the UI sends becomes
 * a request the engine accepts.
 */

import { describe, expect, it, vi } from 'vitest';
import { progressFromStatus, toEngineSegments, wmrEngineCall, type WmrDeps } from './wmrCore';

function deps(overrides: Partial<WmrDeps> = {}) {
  const request = vi.fn(async () => ({}));
  const base: WmrDeps = {
    request: request as unknown as WmrDeps['request'],
    readSettings: () => ({ language: 'zh', theme: 'dark', output: { path: 'C:/out', model_id: 'lama_roi' } }),
    writeSettings: vi.fn(),
    defaultOutputDir: () => 'C:/out',
    openPath: async () => undefined,
    pushProgress: vi.fn(),
    sleep: async () => undefined,
    activeTask: { get: () => null, set: vi.fn() },
    isDirectory: () => true,
    ...overrides,
  };
  return { deps: base, request };
}

describe('toEngineSegments', () => {
  it('rounds and clamps what the canvas produced', () => {
    const [segment] = toEngineSegments([
      {
        start_frame: 3.6,
        end_frame: 9.2,
        rect: { x: 10.4, y: 20.6, width: 0, height: -5 },
      },
    ]) as Array<Record<string, unknown>>;

    expect(segment.start_frame).toBe(4);
    expect(segment.end_frame).toBe(9);
    expect(segment.rect).toEqual({ x: 10, y: 21, width: 1, height: 1 });
    expect(segment.enabled).toBe(true);
  });

  it('keeps an explicit disable and any non-array input is empty', () => {
    const [segment] = toEngineSegments([{ rect: {}, enabled: false }]) as Array<Record<string, unknown>>;
    expect(segment.enabled).toBe(false);
    expect(toEngineSegments(null)).toEqual([]);
    expect(toEngineSegments('nope')).toEqual([]);
  });
});

describe('progressFromStatus', () => {
  it('clamps progress and reports frame counters', () => {
    const detail = progressFromStatus({ progress: 1.7, processed: 10, total: 20, message: '推帧' });
    expect(detail.progress).toBe(1);
    expect(detail.processed_frames).toBe(10);
    expect(detail.total_frames).toBe(20);
    expect(detail.status).toBe('推帧');
  });

  it('formats the remaining time the way the UI prints it', () => {
    const detail = progressFromStatus({ progress: 0.5, eta_seconds: 65 });
    expect(detail.eta_seconds).toBe(65);
    expect(detail.estimated_time).toBe('01:05');
  });

  it('omits time fields when the engine has no estimate yet', () => {
    const detail = progressFromStatus({ progress: 0.1 });
    expect(detail).not.toHaveProperty('eta_seconds');
    expect(detail).not.toHaveProperty('estimated_time');
  });
});

describe('wmrEngineCall', () => {
  it('translates media.probe into the UI media shape', async () => {
    const probe = vi.fn(async () => ({
      width: 640, height: 360, fps: 24, frame_count: 48, duration_seconds: 2,
    }));
    const { deps: d } = deps({ request: probe as unknown as WmrDeps['request'] });

    const info = (await wmrEngineCall(d, 'get_media_info', { path: 'C:/v.mp4' })) as Record<string, unknown>;
    expect(probe).toHaveBeenCalledWith('media.probe', { path: 'C:/v.mp4' });
    expect(info).toMatchObject({ success: true, type: 'video', width: 640, frame_count: 48 });
  });

  it('refuses to process when nothing is marked', async () => {
    const { deps: d, request } = deps();
    const result = (await wmrEngineCall(d, 'process_video', {
      input_path: 'C:/v.mp4',
      annotation_segments: [],
    })) as Record<string, unknown>;

    expect(result.success).toBe(false);
    expect(request).not.toHaveBeenCalled();
  });

  it('polls the task to completion and returns the output path', async () => {
    const responses = [
      { task_id: 't1' },
      { state: 'running', progress: 0.5, processed: 24, total: 48 },
      { state: 'success', progress: 1, result: { output_path: 'C:/out/v_no_watermark.mp4' } },
    ];
    let index = 0;
    const request = vi.fn(async () => responses[Math.min(index++, responses.length - 1)]);
    const pushProgress = vi.fn();
    const { deps: d } = deps({ request: request as unknown as WmrDeps['request'], pushProgress });

    const result = (await wmrEngineCall(d, 'process_video', {
      input_path: 'C:/v.mp4',
      output_path: 'C:/out',
      annotation_segments: [{ start_frame: 0, end_frame: 47, rect: { x: 1, y: 2, width: 3, height: 4 } }],
    })) as Record<string, unknown>;

    expect(result).toMatchObject({ success: true, output_path: 'C:/out/v_no_watermark.mp4' });
    expect(pushProgress).toHaveBeenCalled();
    // The output path is a directory, so the job must not be written next to it.
    expect(request).toHaveBeenCalledWith('process.start', expect.objectContaining({ output_dir: 'C:/out' }));
  });

  it('surfaces a failed task as an error instead of throwing', async () => {
    const responses = [{ task_id: 't1' }, { state: 'failed', error: 'Cannot open video' }];
    let index = 0;
    const { deps: d } = deps({
      request: vi.fn(async () => responses[Math.min(index++, responses.length - 1)]) as unknown as WmrDeps['request'],
    });

    const result = (await wmrEngineCall(d, 'process_video', {
      input_path: 'C:/v.mp4',
      annotation_segments: [{ rect: { x: 0, y: 0, width: 1, height: 1 } }],
    })) as Record<string, unknown>;

    expect(result.success).toBe(false);
    expect(result.error).toContain('Cannot open video');
  });

  it('reports which upscale engines can actually run', async () => {
    const { deps: d } = deps({
      request: vi.fn(async () => ({ models: [{ engine: 'realesrgan', installed: true }] })) as unknown as WmrDeps['request'],
    });

    const capabilities = (await wmrEngineCall(d, 'get_upscale_capabilities', {})) as {
      engines: Array<{ engine: string; available: boolean; reason?: string }>;
    };
    const esrgan = capabilities.engines.find((entry) => entry.engine === 'realesrgan');
    const seedvr = capabilities.engines.find((entry) => entry.engine === 'seedvr2');

    expect(esrgan?.available).toBe(true);
    expect(seedvr?.available).toBe(false);
    expect(seedvr?.reason).toBeTruthy();
  });

  it('maps upscale modes to the scale the engine implements', async () => {
    const request = vi.fn(async () => ({ task_id: 'u1' }));
    const { deps: d } = deps({ request: request as unknown as WmrDeps['request'] });

    await wmrEngineCall(d, 'start_upscale', {
      input_path: 'C:/v.mp4',
      config: { mode: 'enhance_same_resolution' },
    });
    expect(request).toHaveBeenCalledWith('upscale.start', expect.objectContaining({ scale: 2 }));

    request.mockClear();
    await wmrEngineCall(d, 'start_upscale', { input_path: 'C:/v.mp4', config: { mode: 'upscale_resolution' } });
    expect(request).toHaveBeenCalledWith('upscale.start', expect.objectContaining({ scale: 4 }));
  });

  it('reports unknown methods as a value, not an exception', async () => {
    const { deps: d } = deps();
    const result = (await wmrEngineCall(d, 'does_not_exist', {})) as Record<string, unknown>;
    expect(result).toMatchObject({ success: false });
    expect(String(result.error)).toContain('does_not_exist');
  });
});
