// 分析视图的全局状态：任务进度、分析结果、可编辑的水印轨道、当前选中与播放头。
// 编辑规则全部委托给 analysisOps 的纯函数，这里只负责不可变更新、选中修正和结果归属。
import { create } from 'zustand';
import type { Analysis, Rect, WatermarkTrack } from '../../shared/ipc';
import {
  addManualTrack,
  clampIntervals,
  deleteInterval,
  mergeWithNext,
  nudgeInterval,
  setEdgeFrame,
  splitTrack,
  type Edge,
  type EdgeBounds,
} from './analysisOps';

export type AnalysisPhase = 'idle' | 'running' | 'success' | 'failed';

export interface Selection {
  trackId: string;
  intervalIndex: number;
}

export interface InstallState {
  phase: 'idle' | 'running' | 'failed' | 'success';
  progress: number;
  message: string;
  error: string;
}

interface AnalysisState {
  phase: AnalysisPhase;
  taskId: string;
  progress: number;
  message: string;
  error: string;
  analysis: Analysis | null;
  /** 分析结果所属的视频；导入新视频后旧结果不能继续用。 */
  videoPath: string;
  /** Editable copy of `analysis.watermarks`; this is what gets compiled. */
  tracks: WatermarkTrack[];
  selected: Selection | null;
  currentFrame: number;
  dirty: boolean;
  install: InstallState;
  begin: (taskId: string, videoPath?: string) => void;
  setProgress: (progress: number, message: string) => void;
  succeed: (analysis: Analysis, videoPath?: string) => void;
  fail: (error: string) => void;
  reset: () => void;
  setInstall: (patch: Partial<InstallState>) => void;
  markCompiled: () => void;
  select: (selection: Selection | null) => void;
  setFrame: (frame: number) => void;
  nudge: (trackId: string, intervalIndex: number, edge: Edge, delta: number) => void;
  setEdge: (trackId: string, intervalIndex: number, edge: Edge, frame: number) => void;
  split: (trackId: string, intervalIndex: number, frame: number) => void;
  merge: (trackId: string, intervalIndex: number) => void;
  remove: (trackId: string, intervalIndex: number) => void;
  addManual: (rect: Rect, frame: number) => void;
}

const INITIAL_INSTALL: InstallState = { phase: 'idle', progress: 0, message: '', error: '' };

const INITIAL = {
  phase: 'idle' as AnalysisPhase,
  taskId: '',
  progress: 0,
  message: '',
  error: '',
  analysis: null,
  videoPath: '',
  tracks: [] as WatermarkTrack[],
  selected: null,
  currentFrame: 0,
  dirty: false,
  install: INITIAL_INSTALL,
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

/** Neighbour limits so nudging one block cannot silently merge two. */
function neighbourBounds(intervals: WatermarkTrack['intervals'], index: number): EdgeBounds {
  const previous = intervals[index - 1];
  const next = intervals[index + 1];
  return {
    min: previous ? previous.end_frame + 1 : 0,
    max: next ? next.start_frame - 1 : undefined,
  };
}

export const useAnalysisStore = create<AnalysisState>((set, get) => {
  /** Apply a track-level edit and re-normalise + fix the selection. */
  const editTrack = (
    trackId: string,
    update: (track: WatermarkTrack) => WatermarkTrack | null,
    remap?: (selection: Selection | null) => Selection | null,
  ): void => {
    const { tracks, analysis, selected } = get();
    const index = tracks.findIndex((item) => item.id === trackId);
    if (index < 0) {
      return;
    }
    const next = update(tracks[index]!);
    const merged =
      next === null
        ? tracks.filter((item) => item.id !== trackId)
        : [...tracks.slice(0, index), next, ...tracks.slice(index + 1)];
    const clamped = clampIntervals(merged, frameCountOf(analysis));
    const remapped = remap ? remap(selected) : selected;
    set({
      tracks: clamped,
      selected: selectionStillValid(remapped, clamped),
      dirty: true,
    });
  };

  return {
    ...INITIAL,
    begin: (taskId, videoPath) => {
      const current = get();
      const otherVideo = Boolean(videoPath && current.videoPath && videoPath !== current.videoPath);
      // 换了视频就必须丢掉旧结果，否则会把 A 的框套到 B 上。
      const keep = otherVideo ? { analysis: null, tracks: [], selected: null, currentFrame: 0 } : {};
      set({
        ...keep,
        phase: 'running',
        taskId,
        progress: 0,
        message: '正在分析…',
        error: '',
        videoPath: videoPath ?? current.videoPath,
      });
    },
    setProgress: (progress, message) => set({ progress, message }),
    succeed: (analysis, videoPath) => {
      const tracks = clampIntervals(analysis.watermarks ?? [], analysis.video.frame_count);
      set({
        phase: 'success',
        progress: 1,
        message: '',
        error: '',
        analysis,
        tracks,
        videoPath: videoPath ?? get().videoPath,
        selected: tracks.length ? { trackId: tracks[0]!.id, intervalIndex: 0 } : null,
        currentFrame: 0,
        dirty: false,
      });
    },
    fail: (error) => set({ phase: 'failed', error, message: '' }),
    reset: () => set({ ...INITIAL }),
    setInstall: (patch) => set({ install: { ...get().install, ...patch } }),
    markCompiled: () => set({ dirty: false }),
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
        const moved = nudgeInterval(
          interval,
          edge,
          delta,
          frameCountOf(get().analysis),
          neighbourBounds(track.intervals, intervalIndex),
        );
        return {
          ...track,
          intervals: track.intervals.map((item, index) =>
            index === intervalIndex ? moved : item,
          ),
        };
      }),
    setEdge: (trackId, intervalIndex, edge, frame) =>
      editTrack(trackId, (track) => {
        const interval = track.intervals[intervalIndex];
        if (!interval) {
          return track;
        }
        const moved = setEdgeFrame(
          interval,
          edge,
          frame,
          frameCountOf(get().analysis),
          neighbourBounds(track.intervals, intervalIndex),
        );
        return {
          ...track,
          intervals: track.intervals.map((item, index) =>
            index === intervalIndex ? moved : item,
          ),
        };
      }),
    split: (trackId, intervalIndex, frame) =>
      editTrack(
        trackId,
        (track) => splitTrack(track, intervalIndex, frame),
        (selection) =>
          selection && selection.trackId === trackId && selection.intervalIndex > intervalIndex
            ? { ...selection, intervalIndex: selection.intervalIndex + 1 }
            : selection,
      ),
    merge: (trackId, intervalIndex) =>
      editTrack(
        trackId,
        (track) => mergeWithNext(track, intervalIndex),
        (selection) => {
          if (!selection || selection.trackId !== trackId) {
            return selection;
          }
          if (selection.intervalIndex === intervalIndex + 1) {
            return { ...selection, intervalIndex };
          }
          if (selection.intervalIndex > intervalIndex + 1) {
            return { ...selection, intervalIndex: selection.intervalIndex - 1 };
          }
          return selection;
        },
      ),
    remove: (trackId, intervalIndex) =>
      editTrack(
        trackId,
        (track) => deleteInterval(track, intervalIndex),
        (selection) => {
          if (!selection || selection.trackId !== trackId) {
            return selection;
          }
          if (selection.intervalIndex === intervalIndex) {
            return null;
          }
          if (selection.intervalIndex > intervalIndex) {
            return { ...selection, intervalIndex: selection.intervalIndex - 1 };
          }
          return selection;
        },
      ),
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
