// @vitest-environment jsdom
// 报告卡片只做两件事：把分析结果说清楚，以及在缺检测器时给出可执行的下一步。
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AnalysisReport, type InstallState } from './AnalysisReport';
import type { Analysis, DetectorEntry, WatermarkTrack } from '../../shared/ipc';

afterEach(cleanup);

const detector: DetectorEntry = {
  model_id: 'florence2',
  display_name: 'Florence-2 (watermark)',
  installed: true,
  available: true,
  blocked_reason: '',
};

const idleInstall: InstallState = { phase: 'idle', progress: 0, message: '', error: '' };

function track(intervals: Array<[number, number]>, id = 'wm-1'): WatermarkTrack {
  return {
    id,
    intervals: intervals.map(([start, end]) => ({ start_frame: start, end_frame: end })),
    keyframes: [{ frame: intervals[0]![0], rect: { x: 1, y: 1, width: 10, height: 10 } }],
    motion: 'static',
  };
}

function analysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    video: { width: 640, height: 360, fps: 10, frame_count: 100, duration_seconds: 10 },
    shots: [{ start_frame: 0, end_frame: 99 }],
    watermarks: [track([[0, 40]])],
    suggested: { engine_id: 'lama_roi', split_by_shot: false, warnings: [] },
    model_id: 'florence2',
    elapsed_ms: 120,
    ...overrides,
  };
}

function renderReport(overrides: {
  analysis?: Analysis | null;
  tracks?: WatermarkTrack[];
  detector?: DetectorEntry | null;
  install?: InstallState;
  onInstall?: (modelId: string) => void;
} = {}) {
  const value = overrides.analysis === undefined ? analysis() : overrides.analysis;
  return render(
    <AnalysisReport
      analysis={value}
      tracks={overrides.tracks ?? value?.watermarks ?? []}
      detector={overrides.detector === undefined ? detector : overrides.detector}
      install={overrides.install ?? idleInstall}
      onInstall={overrides.onInstall ?? vi.fn()}
    />,
  );
}

describe('with a result', () => {
  it('reports the counts', () => {
    renderReport();
    expect(screen.getByText('分析结果')).toBeTruthy();
    expect(screen.getByText('水印')).toBeTruthy();
    expect(screen.getByText('镜头')).toBeTruthy();
    expect(screen.getByText('区间')).toBeTruthy();
  });

  it('counts the edited tracks, not the raw analysis', () => {
    // 用户把一条拆成两条之后，报告必须跟着变，否则和时间线自相矛盾。
    renderReport({ tracks: [track([[0, 10]], 'a'), track([[11, 40]], 'b')] });
    expect(screen.getByText('水印').parentElement?.textContent).toContain('2');
  });

  it('says so when nothing was found', () => {
    renderReport({
      analysis: analysis({
        watermarks: [],
        suggested: { engine_id: 'lama_roi', split_by_shot: false, warnings: ['no_watermark'] },
      }),
      tracks: [],
    });
    expect(screen.getByText(/没有检测到水印/)).toBeTruthy();
  });

  it('translates known warnings and passes unknown ones through', () => {
    renderReport({
      analysis: analysis({
        suggested: {
          engine_id: 'lama_roi',
          split_by_shot: false,
          warnings: ['moving_watermark', 'something_new'],
        },
      }),
    });
    expect(screen.getByText(/水印在移动/)).toBeTruthy();
    expect(screen.getByText('something_new')).toBeTruthy();
  });

  it('shows nothing before an analysis exists', () => {
    const { container } = renderReport({ analysis: null, tracks: [] });
    expect(container.textContent).toBe('');
  });
});

describe('without a detector', () => {
  const blocked: DetectorEntry = {
    ...detector,
    installed: false,
    available: false,
    blocked_reason: 'detector runtime is not installed',
  };

  it('offers the install and explains why', () => {
    const onInstall = vi.fn();
    renderReport({ analysis: null, detector: blocked, onInstall });
    expect(screen.getByText('需要先安装检测器')).toBeTruthy();
    expect(screen.getByText(/detector runtime is not installed/)).toBeTruthy();
    fireEvent.click(screen.getByText('安装检测器'));
    expect(onInstall).toHaveBeenCalledWith('florence2');
  });

  it('shows progress while installing', () => {
    renderReport({
      analysis: null,
      detector: blocked,
      install: { phase: 'running', progress: 0.42, message: '', error: '' },
    });
    expect(screen.getByText(/42%/)).toBeTruthy();
  });

  it('shows the failure and offers a retry', () => {
    renderReport({
      analysis: null,
      detector: blocked,
      install: { phase: 'failed', progress: 0, message: '', error: 'network down' },
    });
    expect(screen.getByRole('alert').textContent).toContain('network down');
    expect(screen.getByText('重试')).toBeTruthy();
  });
});
