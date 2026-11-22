// 时间线编辑的纯函数：越界、重叠、切分、删除都在这里决定，组件只负责画和转发事件。
// 这些函数不碰 React、不碰 store，输入输出都是契约里的 WatermarkTrack。
import { describe, expect, it } from 'vitest';
import {
  addManualTrack,
  clampIntervals,
  deleteInterval,
  mergeWithNext,
  nudgeInterval,
  splitTrack,
} from './analysisOps';
import type { WatermarkTrack } from '../../shared/ipc';

const FRAMES = 100;

function track(
  intervals: Array<[number, number]>,
  keyframes: number[] = [],
  id = 'wm-1',
): WatermarkTrack {
  return {
    id,
    intervals: intervals.map(([start, end]) => ({ start_frame: start, end_frame: end })),
    keyframes: keyframes.map((frame) => ({
      frame,
      rect: { x: 10, y: 10, width: 20, height: 10 },
    })),
    motion: 'static',
  };
}

function bounds(item: WatermarkTrack): Array<[number, number]> {
  return item.intervals.map((interval) => [interval.start_frame, interval.end_frame]);
}

describe('nudgeInterval', () => {
  it('moves an edge by the requested frames', () => {
    expect(nudgeInterval({ start_frame: 10, end_frame: 20 }, 'end', 5, FRAMES)).toEqual({
      start_frame: 10,
      end_frame: 25,
    });
    expect(nudgeInterval({ start_frame: 10, end_frame: 20 }, 'start', -5, FRAMES)).toEqual({
      start_frame: 5,
      end_frame: 20,
    });
  });

  it('clamps to the video bounds', () => {
    expect(nudgeInterval({ start_frame: 0, end_frame: 20 }, 'start', -10, FRAMES).start_frame).toBe(0);
    expect(
      nudgeInterval({ start_frame: 10, end_frame: FRAMES - 1 }, 'end', 10, FRAMES).end_frame,
    ).toBe(FRAMES - 1);
  });

  it('never lets an edge cross the other one', () => {
    const collapsed = nudgeInterval({ start_frame: 10, end_frame: 20 }, 'start', 50, FRAMES);
    expect(collapsed.start_frame).toBe(20);
    expect(collapsed.end_frame).toBe(20);
  });

  it('does not mutate its input', () => {
    const source = { start_frame: 10, end_frame: 20 };
    nudgeInterval(source, 'end', 5, FRAMES);
    expect(source).toEqual({ start_frame: 10, end_frame: 20 });
  });
});

describe('splitTrack', () => {
  it('splits an interval into two touching halves', () => {
    const split = splitTrack(track([[10, 20]]), 0, 15);
    expect(bounds(split)).toEqual([
      [10, 14],
      [15, 20],
    ]);
  });

  it('keeps keyframes on the side of the cut', () => {
    const split = splitTrack(track([[10, 20]], [12, 18]), 0, 15);
    expect(split.keyframes.map((item) => item.frame)).toEqual([12, 18]);
    expect(split.intervals[0]!.end_frame).toBe(14);
  });

  it('leaves the track alone when the cut is outside the interval', () => {
    const source = track([[10, 20]]);
    expect(splitTrack(source, 0, 10)).toEqual(source);
    expect(splitTrack(source, 0, 21)).toEqual(source);
    expect(splitTrack(source, 5, 15)).toEqual(source);
  });

  it('splits the right interval of a multi-interval track', () => {
    const split = splitTrack(track([[0, 10], [30, 40]]), 1, 35);
    expect(bounds(split)).toEqual([
      [0, 10],
      [30, 34],
      [35, 40],
    ]);
  });
});

describe('mergeWithNext', () => {
  it('joins two intervals into one', () => {
    expect(bounds(mergeWithNext(track([[0, 10], [11, 20]]), 0))).toEqual([[0, 20]]);
  });

  it('joins across a gap because the user asked for one watermark', () => {
    // 跨空洞合并会把"消失"抹掉；这是显式操作，撤销靠再拆分。
    expect(bounds(mergeWithNext(track([[0, 10], [30, 40]]), 0))).toEqual([[0, 40]]);
  });

  it('keeps keyframes from both sides', () => {
    const merged = mergeWithNext(track([[0, 10], [11, 20]], [5, 15]), 0);
    expect(merged.keyframes.map((item) => item.frame)).toEqual([5, 15]);
  });

  it('does nothing for the last interval', () => {
    const source = track([[0, 10], [11, 20]]);
    expect(mergeWithNext(source, 1)).toEqual(source);
  });
});

describe('deleteInterval', () => {
  it('removes one interval and leaves the rest', () => {
    const trimmed = deleteInterval(track([[0, 10], [20, 30]]), 0);
    expect(trimmed).not.toBeNull();
    expect(bounds(trimmed!)).toEqual([[20, 30]]);
  });

  it('drops the whole track when the last interval goes', () => {
    expect(deleteInterval(track([[0, 10]]), 0)).toBeNull();
  });

  it('does not mutate the source track', () => {
    const source = track([[0, 10], [20, 30]]);
    deleteInterval(source, 0);
    expect(bounds(source)).toEqual([[0, 10], [20, 30]]);
  });
});

describe('addManualTrack', () => {
  it('creates a one-frame track at the current frame', () => {
    const tracks = addManualTrack([], { x: 1, y: 2, width: 3, height: 4 }, 42, FRAMES);
    expect(tracks).toHaveLength(1);
    const created = tracks[0]!;
    expect(bounds(created)).toEqual([[42, 42]]);
    expect(created.keyframes).toHaveLength(1);
    expect(created.keyframes[0]!.rect).toEqual({ x: 1, y: 2, width: 3, height: 4 });
    expect(created.motion).toBe('static');
  });

  it('never reuses an id', () => {
    const first = addManualTrack([], { x: 0, y: 0, width: 1, height: 1 }, 5, FRAMES);
    const second = addManualTrack(first, { x: 0, y: 0, width: 1, height: 1 }, 6, FRAMES);
    expect(new Set(second.map((item) => item.id)).size).toBe(2);
  });

  it('clamps a frame outside the video', () => {
    const tracks = addManualTrack([], { x: 0, y: 0, width: 1, height: 1 }, 999, FRAMES);
    expect(bounds(tracks[0]!)).toEqual([[FRAMES - 1, FRAMES - 1]]);
  });
});

describe('clampIntervals', () => {
  it('sorts intervals by start', () => {
    expect(bounds(clampIntervals([track([[30, 40], [0, 10]])], FRAMES)[0]!)).toEqual([
      [0, 10],
      [30, 40],
    ]);
  });

  it('merges overlapping intervals', () => {
    expect(bounds(clampIntervals([track([[0, 10], [5, 20]])], FRAMES)[0]!)).toEqual([[0, 20]]);
  });

  it('keeps a deliberate split of touching intervals', () => {
    // 相邻区间是用户/镜头边界，合并它们等于把分割撤销掉。
    expect(bounds(clampIntervals([track([[0, 10], [11, 20]])], FRAMES)[0]!)).toEqual([
      [0, 10],
      [11, 20],
    ]);
  });

  it('clamps out-of-range intervals to the video', () => {
    expect(bounds(clampIntervals([track([[-5, 5], [90, 200]])], FRAMES)[0]!)).toEqual([
      [0, 5],
      [90, FRAMES - 1],
    ]);
  });

  it('drops a track whose intervals are all outside the video', () => {
    expect(clampIntervals([track([[200, 300]])], FRAMES)).toHaveLength(0);
  });

  it('keeps only the keyframes inside the surviving intervals', () => {
    const clamped = clampIntervals([track([[0, 10]], [5, 50])], FRAMES)[0]!;
    expect(clamped.keyframes.map((item) => item.frame)).toEqual([5]);
  });
});

describe('keyframe invariants', () => {
  it('gives both halves a keyframe after splitting past the only one', () => {
    const split = splitTrack(track([[0, 20]], [18]), 0, 10);
    const [left, right] = split.intervals;
    for (const interval of [left!, right!]) {
      expect(
        split.keyframes.some(
          (keyframe) =>
            keyframe.frame >= interval.start_frame && keyframe.frame <= interval.end_frame,
        ),
        `interval ${interval.start_frame}-${interval.end_frame} has no keyframe`,
      ).toBe(true);
    }
  });

  it('keeps the synthesized keyframe inside the interval it covers', () => {
    const split = splitTrack(track([[0, 20]], [18]), 0, 10);
    const leftKeyframe = split.keyframes.find((item) => item.frame <= 9);
    expect(leftKeyframe).toBeDefined();
    expect(leftKeyframe!.frame).toBe(9);
  });

  it('still covers an interval whose keyframe was nudged out of it', () => {
    const clamped = clampIntervals([track([[6, 10]], [5])], FRAMES)[0]!;
    expect(clamped.keyframes).toHaveLength(1);
    expect(clamped.keyframes[0]!.frame).toBe(6);
  });
});
