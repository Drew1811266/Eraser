// 编译结果 → 打标工作区的段落：字段默认值在这里统一，视图不必知道。
import { describe, expect, it } from 'vitest';
import { segmentsFromCompiled } from './App';
import type { Segment } from '../shared/ipc';

const rect = { x: 10, y: 20, width: 30, height: 40 };

describe('segmentsFromCompiled', () => {
  it('fills the workspace defaults for a compiled segment', () => {
    const [segment] = segmentsFromCompiled([
      { start_frame: 0, end_frame: 49, rect },
    ] as Segment[]);
    expect(segment).toMatchObject({
      start_frame: 0,
      end_frame: 49,
      rect,
      expand_px: 5,
      feather_px: 3,
      enabled: true,
    });
    expect(segment!.id).toMatch(/^seg_analysis_/);
    expect(segment!.created_at).toBeTruthy();
  });

  it('keeps what the engine already set', () => {
    const [segment] = segmentsFromCompiled([
      {
        id: 'wm-1-0',
        start_frame: 10,
        end_frame: 20,
        rect,
        expand_px: 9,
        feather_px: 1,
        enabled: false,
      },
    ]);
    expect(segment).toMatchObject({
      id: 'wm-1-0',
      expand_px: 9,
      feather_px: 1,
      enabled: false,
    });
  });

  it('preserves order and count', () => {
    const mapped = segmentsFromCompiled([
      { start_frame: 0, end_frame: 1, rect },
      { start_frame: 2, end_frame: 3, rect },
      { start_frame: 4, end_frame: 5, rect },
    ] as Segment[]);
    expect(mapped.map((item) => item.start_frame)).toEqual([0, 2, 4]);
  });

  it('produces unique ids', () => {
    const mapped = segmentsFromCompiled([
      { start_frame: 0, end_frame: 1, rect },
      { start_frame: 2, end_frame: 3, rect },
    ] as Segment[]);
    expect(new Set(mapped.map((item) => item.id)).size).toBe(2);
  });
});
