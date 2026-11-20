// @vitest-environment jsdom
/**
 * The shell's state machine, exercised without Electron.
 *
 * Electron cannot start in a headless/CI session (it needs an interactive
 * desktop), so the real app flow is verified twice: here with a fake bridge,
 * and by `scripts/e2e-desktop.mjs` on a machine with a desktop. This test is the
 * one that runs in the normal gate, so it has to cover the parts that actually
 * break: the engine badge, the probe round trip, the progress bar and the
 * cancel/result transitions.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import type { ProbeResult, StartRequest, TaskStatus, UpscaleRequest } from '../shared/ipc';

const PROBE: ProbeResult = {
  path: 'C:/videos/clip.mp4',
  width: 1920,
  height: 1080,
  frame_count: 240,
  fps: 30,
  duration_seconds: 8,
  has_audio: true,
  video_codec: 'h264',
  container: 'mp4',
};

type ProgressListener = (status: TaskStatus) => void;

function createBridge() {
  let listener: ProgressListener | null = null;
  const status: TaskStatus = {
    task_id: 'task-1',
    state: 'running',
    phase: 'infer',
    progress: 0,
    processed: 0,
    total: 240,
    eta_seconds: null,
    message: '处理中…',
  };

  const bridge = {
    capabilities: vi.fn(async () => ({
      engine_version: '0.1.0',
      python_version: '3.13.15',
      platform: 'win32-x86_64',
      methods: ['system.ping'],
      devices: [
        { kind: 'cuda', name: 'NVIDIA GeForce RTX 5090', memory_mb: 24455, available: true },
        { kind: 'cpu', name: 'CPU (32 logical cores)' },
      ],
      runtime: { available_providers: ['CUDAExecutionProvider'], cuda_usable: true, onnxruntime: '1.32.0' },
    })),
    pickVideo: vi.fn(async () => ({ canceled: false, path: PROBE.path })),
    pickOutputDir: vi.fn(async () => ({ canceled: true })),
    probe: vi.fn(async () => PROBE),
    start: vi.fn(async (_request: StartRequest) => ({ task_id: 'task-1' })),
    upscale: vi.fn(async (_request: UpscaleRequest) => ({ task_id: 'task-2' })),
    cancel: vi.fn(async () => ({ ok: true })),
    status: vi.fn(async () => ({ ...status })),
    reveal: vi.fn(async () => undefined),
    openExternal: vi.fn(async () => undefined),
    defaultOutputDir: vi.fn(async () => 'C:/Users/me/Downloads/Eraser'),
    onProgress: vi.fn((fn: ProgressListener) => {
      listener = fn;
      return () => {
        listener = null;
      };
    }),
    onEngineState: vi.fn(() => () => undefined),
    onEngineLog: vi.fn(() => () => undefined),
  };

  return {
    bridge,
    status,
    emit(patch: Partial<TaskStatus>) {
      Object.assign(status, patch);
      listener?.({ ...status });
    },
  };
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('App', () => {
  it('shows the engine state once capabilities arrive', async () => {
    const { bridge } = createBridge();
    window.eraser = bridge as never;

    render(<App />);

    await waitFor(() => expect(screen.getByText(/引擎就绪/)).toBeTruthy());
    expect(screen.getByText(/RTX 5090/)).toBeTruthy();
  });

  it('surfaces an engine startup failure instead of hanging', async () => {
    const { bridge } = createBridge();
    bridge.capabilities.mockRejectedValue(new Error('spawn ENOENT'));
    window.eraser = bridge as never;

    render(<App />);

    await waitFor(() => expect(screen.getByText(/引擎异常/)).toBeTruthy());
  });

  it('probes a chosen video and suggests a selection', async () => {
    const { bridge } = createBridge();
    window.eraser = bridge as never;

    render(<App />);
    await waitFor(() => expect(screen.getByText(/引擎就绪/)).toBeTruthy());

    fireEvent.click(screen.getByText('打开视频'));

    await waitFor(() => expect(bridge.probe).toHaveBeenCalledWith(PROBE.path));
    await waitFor(() => expect(screen.getByText(/1920 × 1080/)).toBeTruthy());
    expect(screen.getByText(/240 帧/)).toBeTruthy();
    // The suggested box starts in the top-right, and its numbers are editable.
    const inputs = screen.getAllByRole('spinbutton') as HTMLInputElement[];
    expect(inputs.length).toBe(4);
    expect(Number(inputs[0]!.value)).toBeGreaterThan(1000);
  });

  it('streams progress into the bar and finishes with a playable path', async () => {
    const { bridge, emit } = createBridge();
    window.eraser = bridge as never;

    render(<App />);
    await waitFor(() => expect(screen.getByText(/引擎就绪/)).toBeTruthy());
    fireEvent.click(screen.getByText('打开视频'));
    await waitFor(() => expect(screen.getByText(/1920 × 1080/)).toBeTruthy());

    fireEvent.click(screen.getByText('开始擦除'));
    await waitFor(() => expect(bridge.start).toHaveBeenCalledTimes(1));

    const request = bridge.start.mock.calls[0]![0];
    expect(request.segments[0]!.end_frame).toBe(239);
    expect(request.segments[0]!.rect.width).toBeGreaterThan(0);
    expect(request.input_path).toBe(PROBE.path);

    await act(async () => {
      emit({ progress: 0.25, processed: 60, message: '推理中' });
    });
    await waitFor(() => expect(screen.getByText('25.0%')).toBeTruthy());
    expect(screen.getByText(/60 \/ 240 帧/)).toBeTruthy();

    await act(async () => {
      emit({
        state: 'success',
        progress: 1,
        processed: 240,
        message: 'Done',
        result: {
          output_path: 'C:/Users/me/Downloads/Eraser/clip_no_watermark.mp4',
          requested_model_id: 'lama_roi',
          effective_model_id: 'lama_roi',
          model_warning: '',
        },
      });
    });

    await waitFor(() => expect(screen.getByText('完成')).toBeTruthy());
    expect(screen.getByText(/clip_no_watermark\.mp4/)).toBeTruthy();

    fireEvent.click(screen.getByText('打开所在文件夹'));
    expect(bridge.reveal).toHaveBeenCalledWith('C:/Users/me/Downloads/Eraser/clip_no_watermark.mp4');

    // The result can be enlarged further; the request must point at the file
    // that was just produced, not the original.
    fireEvent.click(screen.getByText('放大 4×'));
    await waitFor(() => expect(bridge.upscale).toHaveBeenCalledTimes(1));
    const upscaleRequest = bridge.upscale.mock.calls[0]![0];
    expect(upscaleRequest.input_path).toBe('C:/Users/me/Downloads/Eraser/clip_no_watermark.mp4');
    expect(upscaleRequest.scale).toBe(4);
  });

  it('can cancel a running job', async () => {
    const { bridge, emit } = createBridge();
    window.eraser = bridge as never;

    render(<App />);
    await waitFor(() => expect(screen.getByText(/引擎就绪/)).toBeTruthy());
    fireEvent.click(screen.getByText('打开视频'));
    await waitFor(() => expect(screen.getByText(/1920 × 1080/)).toBeTruthy());
    fireEvent.click(screen.getByText('开始擦除'));

    await waitFor(() => expect(screen.getByText('取消')).toBeTruthy());
    fireEvent.click(screen.getByText('取消'));
    expect(bridge.cancel).toHaveBeenCalledWith('task-1');

    await act(async () => {
      emit({ state: 'cancelled', message: 'Cancelled' });
    });
    await waitFor(() => expect(screen.getByText('已取消')).toBeTruthy());
  });

  it('shows the engine error when a job fails', async () => {
    const { bridge, emit } = createBridge();
    window.eraser = bridge as never;

    render(<App />);
    await waitFor(() => expect(screen.getByText(/引擎就绪/)).toBeTruthy());
    fireEvent.click(screen.getByText('打开视频'));
    await waitFor(() => expect(screen.getByText(/1920 × 1080/)).toBeTruthy());
    fireEvent.click(screen.getByText('开始擦除'));
    await waitFor(() => expect(bridge.start).toHaveBeenCalled());

    await act(async () => {
      emit({ state: 'failed', error: 'MODEL_MISSING: big-lama.onnx' });
    });

    await waitFor(() => expect(screen.getByText(/MODEL_MISSING/)).toBeTruthy());
  });
});
