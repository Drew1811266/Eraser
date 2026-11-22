// 分析 store 的行为：编辑后必须重新归一化，选中的东西不能指向不存在的轨道。
import { beforeEach, describe, expect, it } from 'vitest';
import { useAnalysisStore } from './analysis';
import type { Analysis, WatermarkTrack } from '../../shared/ipc';

function track(intervals: Array<[number, number]>, id = 'wm-1'): WatermarkTrack {
  return {
    id,
    intervals: intervals.map(([start, end]) => ({ start_frame: start, end_frame: end })),
    keyframes: [{ frame: intervals[0]![0], rect: { x: 0, y: 0, width: 10, height: 10 } }],
    motion: 'static',
  };
}

function analysis(watermarks: WatermarkTrack[] = [track([[0, 10]])]): Analysis {
  return {
    video: { width: 640, height: 360, fps: 10, frame_count: 100, duration_seconds: 10 },
    shots: [{ start_frame: 0, end_frame: 99 }],
    watermarks,
    suggested: { engine_id: 'lama_roi', split_by_shot: false, warnings: [] },
    model_id: 'florence2',
    elapsed_ms: 5,
  };
}

beforeEach(() => {
  useAnalysisStore.getState().reset();
});

describe('succeed', () => {
  it('copies the watermarks and selects the first one', () => {
    useAnalysisStore.getState().succeed(analysis());
    const state = useAnalysisStore.getState();
    expect(state.phase).toBe('success');
    expect(state.tracks).toHaveLength(1);
    expect(state.selected).toEqual({ trackId: 'wm-1', intervalIndex: 0 });
    expect(state.dirty).toBe(false);
  });

  it('accepts a clean video with no watermarks', () => {
    useAnalysisStore.getState().succeed(analysis([]));
    expect(useAnalysisStore.getState().tracks).toEqual([]);
    expect(useAnalysisStore.getState().selected).toBeNull();
  });
});

describe('editing', () => {
  it('marks the state dirty after a nudge', () => {
    useAnalysisStore.getState().succeed(analysis());
    useAnalysisStore.getState().nudge('wm-1', 0, 'start', 5);
    const state = useAnalysisStore.getState();
    expect(state.dirty).toBe(true);
    expect(state.tracks[0]!.intervals[0]!.start_frame).toBe(5);
  });

  it('drops the track and the selection when the last interval is deleted', () => {
    useAnalysisStore.getState().succeed(analysis());
    useAnalysisStore.getState().remove('wm-1', 0);
    const state = useAnalysisStore.getState();
    expect(state.tracks).toEqual([]);
    expect(state.selected).toBeNull();
  });

  it('keeps the selection on a surviving interval after a split', () => {
    useAnalysisStore.getState().succeed(analysis([track([[0, 20]])]));
    useAnalysisStore.getState().split('wm-1', 0, 10);
    const state = useAnalysisStore.getState();
    expect(state.tracks[0]!.intervals).toHaveLength(2);
    expect(state.selected).toEqual({ trackId: 'wm-1', intervalIndex: 0 });
  });

  it('merges two intervals into one', () => {
    useAnalysisStore.getState().succeed(analysis([track([[0, 5], [6, 20]])]));
    useAnalysisStore.getState().merge('wm-1', 0);
    expect(useAnalysisStore.getState().tracks[0]!.intervals).toEqual([
      { start_frame: 0, end_frame: 20 },
    ]);
  });

  it('adds a manual watermark at the current frame and selects it', () => {
    useAnalysisStore.getState().succeed(analysis([]));
    useAnalysisStore.getState().addManual({ x: 1, y: 2, width: 3, height: 4 }, 42);
    const state = useAnalysisStore.getState();
    expect(state.tracks).toHaveLength(1);
    expect(state.tracks[0]!.intervals[0]).toEqual({ start_frame: 42, end_frame: 42 });
    expect(state.selected?.trackId).toBe(state.tracks[0]!.id);
  });

  it('ignores edits for an unknown track', () => {
    useAnalysisStore.getState().succeed(analysis());
    useAnalysisStore.getState().nudge('nope', 0, 'end', 5);
    expect(useAnalysisStore.getState().tracks[0]!.intervals[0]!.end_frame).toBe(10);
  });
});

describe('playhead', () => {
  it('clamps to the video length', () => {
    useAnalysisStore.getState().succeed(analysis());
    useAnalysisStore.getState().setFrame(500);
    expect(useAnalysisStore.getState().currentFrame).toBe(99);
    useAnalysisStore.getState().setFrame(-5);
    expect(useAnalysisStore.getState().currentFrame).toBe(0);
  });
});

describe('failure', () => {
  it('keeps the reason so the view can show it', () => {
    useAnalysisStore.getState().fail('检测器未安装');
    expect(useAnalysisStore.getState().phase).toBe('failed');
    expect(useAnalysisStore.getState().error).toBe('检测器未安装');
  });

  it('reset returns to idle', () => {
    useAnalysisStore.getState().succeed(analysis());
    useAnalysisStore.getState().reset();
    expect(useAnalysisStore.getState()).toMatchObject({ phase: 'idle', tracks: [], analysis: null });
  });
});
