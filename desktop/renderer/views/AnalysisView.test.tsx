// @vitest-environment jsdom
// 分析视图把四件事串起来：查能力 → 装检测器 → 分析 → 编辑后编译交给处理。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AnalysisView } from './AnalysisView';
import { useAnalysisStore } from '../store/analysis';
import type { CompileRequest, DetectorEntry, Segment } from '../../shared/ipc';

afterEach(cleanup);

const florence: DetectorEntry = {
  model_id: 'florence2',
  display_name: 'Florence-2 (watermark)',
  installed: true,
  available: true,
  blocked_reason: '',
};

const blocked: DetectorEntry = { ...florence, installed: false, available: false, blocked_reason: 'detector runtime is not installed' };

const analysisPayload = {
  video: { width: 640, height: 360, fps: 10, frame_count: 100, duration_seconds: 10 },
  shots: [{ start_frame: 0, end_frame: 49 }, { start_frame: 50, end_frame: 99 }],
  watermarks: [
    {
      id: 'wm-1',
      intervals: [{ start_frame: 0, end_frame: 49 }],
      keyframes: [{ frame: 0, rect: { x: 10, y: 10, width: 40, height: 20 } }],
      motion: 'static',
    },
  ],
  suggested: { engine_id: 'lama_roi', split_by_shot: true, warnings: [] },
  model_id: 'florence2',
  elapsed_ms: 120,
};

function bridge(overrides: Record<string, unknown> = {}) {
  const api = {
    analyzeCapabilities: vi.fn(async () => ({ detectors: [florence], available: true })),
    analyzeStart: vi.fn(async () => ({ task_id: 'analyze-1' })),
    compileAnnotations: vi.fn(async (_request: CompileRequest) => ({
      segments: [{ start_frame: 0, end_frame: 49, rect: { x: 10, y: 10, width: 40, height: 20 } }],
    })),
    installDetector: vi.fn(async () => ({ task_id: 'install-1' })),
    status: vi.fn(async () => ({ task_id: 'analyze-1', state: 'success', result: analysisPayload })),
    ...overrides,
  };
  (window as unknown as { eraser: typeof api }).eraser = api;
  return api;
}

beforeEach(() => {
  useAnalysisStore.getState().reset();
});

function renderView(api: ReturnType<typeof bridge>, onStart = vi.fn(async (_segments: Segment[]) => {})) {
  render(
    <AnalysisView
      videoPath="C:/v.mp4"
      videoMeta={{ width: 640, height: 360, frame_count: 100, fps: 10 }}
      frameImageUrl="blob:frame"
      onStart={onStart}
    />,
  );
  return { api, onStart };
}

describe('detector gate', () => {
  it('offers the install when no detector is available', async () => {
    const api = bridge({ analyzeCapabilities: vi.fn(async () => ({ detectors: [blocked], available: false })) });
    renderView(api);
    expect(await screen.findByText('需要先安装检测器')).toBeTruthy();
    expect(screen.queryByText('开始处理')).toBeNull();
  });

  it('installs, polls and then allows analysing', async () => {
    let installed = false;
    const api = bridge({
      analyzeCapabilities: vi.fn(async () => ({
        detectors: [installed ? florence : blocked],
        available: installed,
      })),
      // 安装成功发生在调用内部；刷新能力时它必须已经变了，否则测的不是真实顺序。
      installDetector: vi.fn(async () => {
        installed = true;
        return { task_id: 'install-1' };
      }),
      status: vi.fn(async () => ({ task_id: 'install-1', state: 'success', result: { phase: 'ready' } })),
    });
    renderView(api);
    fireEvent.click(await screen.findByText('安装检测器'));
    await waitFor(() => expect(screen.queryByText('需要先安装检测器')).toBeNull());
    expect(api.installDetector).toHaveBeenCalledWith({ model_id: 'florence2' });
    expect(api.analyzeCapabilities).toHaveBeenCalledTimes(2);
    expect(await screen.findByText('开始分析')).toBeTruthy();
  });
});

describe('analysis', () => {
  it('runs the analysis and shows the report', async () => {
    const api = bridge();
    renderView(api);
    fireEvent.click(await screen.findByText('开始分析'));
    await waitFor(() => expect(api.analyzeStart).toHaveBeenCalledWith({ input_path: 'C:/v.mp4' }));
    expect(await screen.findByText('分析结果')).toBeTruthy();
    expect(screen.getByText('wm-1')).toBeTruthy();
  });

  it('shows why the analysis failed', async () => {
    const api = bridge({
      status: vi.fn(async () => ({ task_id: 'analyze-1', state: 'failed', error: 'DETECTOR_MISSING: 没有检测器' })),
    });
    renderView(api);
    fireEvent.click(await screen.findByText('开始分析'));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('DETECTOR_MISSING');
  });
});

describe('editing and handing off', () => {
  it('splits the selected interval on the timeline', async () => {
    const api = bridge();
    renderView(api);
    fireEvent.click(await screen.findByText('开始分析'));
    await screen.findByText('分析结果');
    expect(screen.getAllByTestId(/^block-/)).toHaveLength(1);

    act(() => useAnalysisStore.getState().setFrame(20));
    fireEvent.click(screen.getByText('拆分'));
    expect(screen.getAllByTestId(/^block-/)).toHaveLength(2);
  });

  it('compiles the edited tracks and hands the segments to the app', async () => {
    const api = bridge();
    const { onStart } = renderView(api);
    fireEvent.click(await screen.findByText('开始分析'));
    await screen.findByText('分析结果');

    fireEvent.click(screen.getByText('开始处理'));

    await waitFor(() => expect(api.compileAnnotations).toHaveBeenCalled());
    const payload = api.compileAnnotations.mock.calls[0]![0];
    expect(payload.watermarks[0]!.id).toBe('wm-1');
    expect(payload.video.frame_count).toBe(100);
    await waitFor(() => expect(onStart).toHaveBeenCalled());
    expect(onStart.mock.calls[0]![0]).toEqual([
      { start_frame: 0, end_frame: 49, rect: { x: 10, y: 10, width: 40, height: 20 } },
    ]);
  });
});
