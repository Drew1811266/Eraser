// 时间线编辑的纯函数：组件只负责画和转发事件，规则都在这里。
// 区间是闭区间 [start_frame, end_frame]，与契约里的 Segment 保持一致。
import type { FrameInterval, Rect, RectKeyframe, WatermarkTrack } from '../../shared/ipc';

export type Edge = 'start' | 'end';

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function lastFrame(frameCount: number): number {
  return Math.max(0, frameCount - 1);
}

function inside(frame: number, intervals: FrameInterval[]): boolean {
  return intervals.some(
    (interval) => frame >= interval.start_frame && frame <= interval.end_frame,
  );
}

/** Drop keyframes whose frame no longer belongs to any interval. */
function pruneKeyframes(keyframes: RectKeyframe[], intervals: FrameInterval[]): RectKeyframe[] {
  return keyframes.filter((keyframe) => inside(keyframe.frame, intervals));
}

/**
 * Keep every interval covered by at least one keyframe.
 *
 * The compiler samples rectangles per interval; an interval with no keyframe has
 * nothing to sample, so a split that leaves one side empty borrows the nearest
 * rectangle instead of emitting a hole.
 */
function ensureKeyframes(
  keyframes: RectKeyframe[],
  intervals: FrameInterval[],
): RectKeyframe[] {
  const kept = pruneKeyframes(keyframes, intervals);
  if (!kept.length || !keyframes.length) {
    return kept;
  }
  const filled = [...kept];
  for (const interval of intervals) {
    if (filled.some((keyframe) => inside(keyframe.frame, [interval]))) {
      continue;
    }
    const nearest = keyframes.reduce((best, candidate) =>
      Math.abs(candidate.frame - interval.start_frame) < Math.abs(best.frame - interval.start_frame)
        ? candidate
        : best,
    );
    filled.push({ frame: interval.start_frame, rect: { ...nearest.rect } });
  }
  return filled.sort((a, b) => a.frame - b.frame);
}

/** Move one edge; an edge may close the interval but never cross the other edge. */
export function nudgeInterval(
  interval: FrameInterval,
  edge: Edge,
  delta: number,
  frameCount: number,
): FrameInterval {
  const last = lastFrame(frameCount);
  const start = clamp(interval.start_frame, 0, last);
  const end = clamp(interval.end_frame, start, last);
  if (edge === 'start') {
    return { start_frame: clamp(start + delta, 0, end), end_frame: end };
  }
  return { start_frame: start, end_frame: clamp(end + delta, start, last) };
}

/** Cut `frame` off the start of the following half. No-op when the cut misses. */
export function splitTrack(
  track: WatermarkTrack,
  intervalIndex: number,
  frame: number,
): WatermarkTrack {
  const interval = track.intervals[intervalIndex];
  if (!interval || frame <= interval.start_frame || frame > interval.end_frame) {
    return track;
  }
  const intervals: FrameInterval[] = [
    ...track.intervals.slice(0, intervalIndex),
    { start_frame: interval.start_frame, end_frame: frame - 1 },
    { start_frame: frame, end_frame: interval.end_frame },
    ...track.intervals.slice(intervalIndex + 1),
  ];
  return { ...track, intervals, keyframes: ensureKeyframes(track.keyframes, intervals) };
}

/** Join two intervals. The user is asserting one watermark, so a gap disappears. */
export function mergeWithNext(track: WatermarkTrack, intervalIndex: number): WatermarkTrack {
  const first = track.intervals[intervalIndex];
  const second = track.intervals[intervalIndex + 1];
  if (!first || !second) {
    return track;
  }
  const intervals: FrameInterval[] = [
    ...track.intervals.slice(0, intervalIndex),
    {
      start_frame: Math.min(first.start_frame, second.start_frame),
      end_frame: Math.max(first.end_frame, second.end_frame),
    },
    ...track.intervals.slice(intervalIndex + 2),
  ];
  return { ...track, intervals, keyframes: ensureKeyframes(track.keyframes, intervals) };
}

/** Remove one interval; the caller drops the track when this returns null. */
export function deleteInterval(
  track: WatermarkTrack,
  intervalIndex: number,
): WatermarkTrack | null {
  if (!track.intervals[intervalIndex]) {
    return track;
  }
  const intervals = track.intervals.filter((_item, index) => index !== intervalIndex);
  if (!intervals.length) {
    return null;
  }
  return { ...track, intervals, keyframes: ensureKeyframes(track.keyframes, intervals) };
}

/** Add a one-frame watermark the detector missed. */
export function addManualTrack(
  tracks: WatermarkTrack[],
  rect: Rect,
  frame: number,
  frameCount: number,
): WatermarkTrack[] {
  const at = clamp(Math.round(frame), 0, lastFrame(frameCount));
  const used = new Set(tracks.map((track) => track.id));
  let serial = tracks.length + 1;
  while (used.has(`wm-manual-${serial}`)) {
    serial += 1;
  }
  return [
    ...tracks,
    {
      id: `wm-manual-${serial}`,
      intervals: [{ start_frame: at, end_frame: at }],
      keyframes: [{ frame: at, rect: { ...rect } }],
      motion: 'static',
      confidence: 1,
      source: 'manual',
    },
  ];
}

/** Normalise after edits: clamp, sort, merge overlap, prune keyframes, drop empties. */
export function clampIntervals(
  tracks: WatermarkTrack[],
  frameCount: number,
): WatermarkTrack[] {
  const last = lastFrame(frameCount);
  const result: WatermarkTrack[] = [];
  for (const track of tracks) {
    const clipped = track.intervals
      .filter((interval) => interval.end_frame >= 0 && interval.start_frame <= last)
      .map((interval) => ({
        start_frame: clamp(interval.start_frame, 0, last),
        end_frame: clamp(interval.end_frame, 0, last),
      }))
      .filter((interval) => interval.start_frame <= interval.end_frame)
      .sort((a, b) => a.start_frame - b.start_frame);

    const merged: FrameInterval[] = [];
    for (const interval of clipped) {
      const previous = merged[merged.length - 1];
      // Only overlap is merged. Two touching intervals are a deliberate split
      // (a user boundary, or a per-shot cut) and must survive normalisation.
      if (previous && interval.start_frame <= previous.end_frame) {
        previous.end_frame = Math.max(previous.end_frame, interval.end_frame);
      } else {
        merged.push({ ...interval });
      }
    }
    if (!merged.length) {
      continue;
    }
    result.push({ ...track, intervals: merged, keyframes: pruneKeyframes(track.keyframes, merged) });
  }
  return result;
}
