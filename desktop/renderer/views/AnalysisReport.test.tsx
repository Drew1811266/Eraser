// @vitest-environment jsdom
// 报告卡片只做两件事：把分析结果说清楚，以及在缺检测器时给出可执行的下一步。
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AnalysisReport, type InstallState } from './AnalysisReport';
import type { Analysis, DetectorEntry } from '../../shared/ipc';

afterEach(cleanup);

const detector: DetectorEntry = {
  model_id: 'florence2',
  display_name: 'Florence-2 (watermark)',
  installed: true,
  available: true,
  blocked_reason: '',
};

const idleInstall: InstallState = { phase: 'idle', progress: 0, message: '', error: '' };

function analysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    video: { width: 640, height: 360, fps: 10, frame_count: 100, duration_seconds: 10 },
    shots: [{ start_frame: 0, end_frame: 99 }],
    watermarks: [
      {
        id: 'wm-1',
        intervals: [{ start_frame: 0, end_frame: 40 }],
        keyframes: [{ frame: 0, rect: { x: 1, y: 1, width: 10, height: 10 } }],
        motion: 'static',
      },
    ],
    suggested: { engine_id: 'lama_roi', split_by_shot: false, warnings: [] },
    model_id: 'florence2',
    elapsed_ms: 120,
    ...overrides,
  };
}

describe('with a result', () => {
  it('reports the counts', () => {
    render(<AnalysisReport analysis={analysis()} detector={detector} install={idleInstall} onInstall={vi.fn()} />);
    expect(screen.getByText('分析结果')).toBeTruthy();
    expect(screen.getByText('水印')).toBeTruthy();
    expect(screen.getByText('镜头')).toBeTruthy();
  });

  it('says so when nothing was found', () => {
    render(
      <AnalysisReport
        analysis={analysis({ watermarks: [], suggested: { engine_id: 'lama_roi', split_by_shot: false, warnings: ['no_watermark'] } })}
        detector={detector}
        install={idleInstall}
        onInstall={vi.fn()}
      />,
    );
    expect(screen.getByText(/没有检测到水印/)).toBeTruthy();
  });

  it('translates known warnings and passes unknown ones through', () => {
    render(
      <AnalysisReport
        analysis={analysis({
          suggested: {
            engine_id: 'lama_roi',
            split_by_shot: false,
            warnings: ['moving_watermark', 'something_new'],
          },
        })}
        detector={detector}
        install={idleInstall}
        onInstall={vi.fn()}
      />,
    );
    expect(screen.getByText(/水印在移动/)).toBeTruthy();
    expect(screen.getByText('something_new')).toBeTruthy();
  });

  it('shows nothing before an analysis exists', () => {
    const { container } = render(
      <AnalysisReport analysis={null} detector={detector} install={idleInstall} onInstall={vi.fn()} />,
    );
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
    render(<AnalysisReport analysis={null} detector={blocked} install={idleInstall} onInstall={onInstall} />);
    expect(screen.getByText('需要先安装检测器')).toBeTruthy();
    expect(screen.getByText(/detector runtime is not installed/)).toBeTruthy();
    fireEvent.click(screen.getByText('安装检测器'));
    expect(onInstall).toHaveBeenCalledWith('florence2');
  });

  it('shows progress while installing', () => {
    render(
      <AnalysisReport
        analysis={null}
        detector={blocked}
        install={{ phase: 'running', progress: 0.42, message: '', error: '' }}
        onInstall={vi.fn()}
      />,
    );
    expect(screen.getByText(/42%/)).toBeTruthy();
  });

  it('shows the failure and offers a retry', () => {
    render(
      <AnalysisReport
        analysis={null}
        detector={blocked}
        install={{ phase: 'failed', progress: 0, message: '', error: 'network down' }}
        onInstall={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert').textContent).toContain('network down');
    expect(screen.getByText('重试')).toBeTruthy();
  });
});
