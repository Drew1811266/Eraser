// 分析视图的全局状态：任务进度、分析结果、可编辑的水印轨道、当前选中与播放头。
// 编辑规则全部委托给 analysisOps 的纯函数，这里只负责不可变更新和状态收尾。
import { create } from 'zustand';
import type { Analysis, Rect, WatermarkTrack } from '../../shared/ipc';
import {
  addManualTrack,
  clampIntervals,
  deleteInterval,
  mergeWithNext,
  nudgeInterval,
  splitTrack,
  type Edge,
} from './analysisOps';

export type AnalysisPhase = 'idle' | 'running' | 'success' | 'failed';

export interface Selection {
  trackId: string;
  intervalIndex: number;
}

interface AnalysisState {
  phase: AnalysisPhase;
  taskId: string;
  progress: number;
  message: string;
  error: string;
  analysis: Analysis | null;
  /** Editable copy of `analysis.watermarks`; this is what gets compiled. */
  tracks: WatermarkTrack[];
  selected: Selection | null;
  currentFrame: number;
  dirty: boolean;
  begin: (taskId: string) => void;
  setProgress: (progress: number, message: string) => void;
  succeed: (analysis: Analysis) => void;
  fail: (error: string) => void;
  reset: () => void;
  select: (selection: Selection | null) => void;
  setFrame: (frame: number) => void;
  nudge: (trackId: string, intervalIndex: number, edge: Edge, delta: number) => void;
  split: (trackId: string, intervalIndex: number, frame: number) => void;
  merge: (trackId: string, intervalIndex: number) => void;
  remove: (trackId: string, intervalIndex: number) => void;
  addManual: (rect: Rect, frame: number) => void;
}

const INITIAL = {
  phase: 'idle' as AnalysisPhase,
  taskId: '',
  progress: 0,
  message: '',
  error: '',
  analysis: null,
  tracks: [] as WatermarkTrack[],
  selected: null,
  currentFrame: 0,
  dirty: false,
};

function frameCountOf(analysis: Analysis | null): number {
  return analysis?.video.frame_count ?? 0;
}

function selectionStillValid(
  selection: Selection | null,
  tracks: WatermarkTrack[],
): Selection | null {
  if (!selection) {
    return null;
  }
  const track = tracks.find((item) => item.id === selection.trackId);
  if (!track || !track.intervals[selection.intervalIndex]) {
    return null;
  }
  return selection;
}

export const useAnalysisStore = create<AnalysisState>((set, get) => {
  /** Apply a track-level edit and re-normalise everything it could have broken. */
  const editTrack = (
    trackId: string,
    update: (track: WatermarkTrack) => WatermarkTrack | null,
  ): void => {
    const { tracks, analysis, selected } = get();
    const index = tracks.findIndex((item) => item.id === trackId);
    if (index < 0) {
      return;
    }
    const next = update(tracks[index]!);
    const replaced = next === null ? [] : [next];
    const merged =
      next === null
        ? tracks.filter((item) => item.id !== trackId)
        : [...tracks.slice(0, index), ...replaced, ...tracks.slice(index + 1)];
    const clamped = clampIntervals(merged, frameCountOf(analysis));
    set({
      tracks: clamped,
      selected: selectionStillValid(selected, clamped),
      dirty: true,
    });
  };

  return {
    ...INITIAL,
    begin: (taskId) =>
      set({ ...INITIAL, phase: 'running', taskId, message: '正在分析…' }),
    setProgress: (progress, message) => set({ progress, message }),
    succeed: (analysis) => {
      const tracks = clampIntervals(analysis.watermarks ?? [], analysis.video.frame_count);
      set({
        phase: 'success',
        progress: 1,
        message: '',
        error: '',
        analysis,
        tracks,
        selected: tracks.length ? { trackId: tracks[0]!.id, intervalIndex: 0 } : null,
        currentFrame: 0,
        dirty: false,
      });
    },
    fail: (error) => set({ phase: 'failed', error, message: '' }),
    reset: () => set({ ...INITIAL }),
    select: (selected) => set({ selected }),
    setFrame: (frame) => {
      const total = frameCountOf(get().analysis);
      const last = Math.max(0, total - 1);
      set({ currentFrame: Math.min(last, Math.max(0, Math.round(frame))) });
    },
    nudge: (trackId, intervalIndex, edge, delta) =>
      editTrack(trackId, (track) => {
        const interval = track.intervals[intervalIndex];
        if (!interval) {
          return track;
        }
        const intervals = track.intervals.map((item, index) =>
          index === intervalIndex
            ? nudgeInterval(interval, edge, delta, frameCountOf(get().analysis))
            : item,
        );
        return { ...track, intervals };
      }),
    split: (trackId, intervalIndex, frame) =>
      editTrack(trackId, (track) => splitTrack(track, intervalIndex, frame)),
    merge: (trackId, intervalIndex) =>
      editTrack(trackId, (track) => mergeWithNext(track, intervalIndex)),
    remove: (trackId, intervalIndex) =>
      editTrack(trackId, (track) => deleteInterval(track, intervalIndex)),
    addManual: (rect, frame) => {
      const { tracks, analysis } = get();
      const next = addManualTrack(tracks, rect, frame, frameCountOf(analysis));
      const created = next[next.length - 1];
      set({
        tracks: next,
        selected: created ? { trackId: created.id, intervalIndex: 0 } : null,
        dirty: true,
      });
    },
  };
});
