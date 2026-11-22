// @vitest-environment jsdom
// 时间线：泳道、区间块、镜头切点、播放头。位置用百分比表示，不依赖测量。
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Timeline } from './Timeline';
import type { WatermarkTrack } from '../../shared/ipc';

afterEach(cleanup);

function track(intervals: Array<[number, number]>, id = 'wm-1'): WatermarkTrack {
  return {
    id,
    intervals: intervals.map(([start, end]) => ({ start_frame: start, end_frame: end })),
    keyframes: [{ frame: 0, rect: { x: 0, y: 0, width: 10, height: 10 } }],
    motion: 'static',
  };
}

function renderTimeline(overrides: Partial<Parameters<typeof Timeline>[0]> = {}) {
  const props = {
    tracks: [track([[0, 9], [20, 29]])],
    shots: [
      { start_frame: 0, end_frame: 49 },
      { start_frame: 50, end_frame: 99 },
    ],
    frameCount: 100,
    fps: 10,
    currentFrame: 0,
    selected: null,
    onSelect: vi.fn(),
    onSeek: vi.fn(),
    onNudge: vi.fn(),
    ...overrides,
  };
  const view = render(<Timeline {...props} />);
  return { ...props, ...view };
}

describe('layout', () => {
  it('renders one lane per track and one block per interval', () => {
    renderTimeline({
      tracks: [track([[0, 9], [20, 29]], 'a'), track([[60, 70]], 'b')],
    });
    expect(screen.getAllByTestId(/^lane-/)).toHaveLength(2);
    expect(screen.getAllByTestId(/^block-/)).toHaveLength(3);
  });

  it('places blocks by frame proportion', () => {
    renderTimeline();
    const first = screen.getByTestId('block-wm-1-0');
    expect(first.style.left).toBe('0%');
    expect(first.style.width).toBe('10%');
    const second = screen.getByTestId('block-wm-1-1');
    expect(second.style.left).toBe('20%');
    expect(second.style.width).toBe('10%');
  });

  it('draws a marker for every shot after the first', () => {
    renderTimeline();
    expect(screen.getAllByTestId(/^cut-/)).toHaveLength(1);
  });

  it('shows the playhead at the current frame', () => {
    renderTimeline({ currentFrame: 50 });
    expect(screen.getByTestId('playhead').style.left).toBe('50%');
  });
});

describe('interaction', () => {
  it('selects a block when clicked', () => {
    const onSelect = vi.fn();
    renderTimeline({ onSelect });
    fireEvent.click(screen.getByTestId('block-wm-1-1'));
    expect(onSelect).toHaveBeenCalledWith('wm-1', 1);
  });

  it('offers nudge buttons on the selected block only', () => {
    const onSelect = vi.fn();
    renderTimeline({ onSelect });
    expect(screen.queryByLabelText('起点前移一帧')).toBeNull();
  });

  it('nudges the start and end by one frame', () => {
    const onNudge = vi.fn();
    renderTimeline({ onNudge, selected: { trackId: 'wm-1', intervalIndex: 1 } });
    fireEvent.click(screen.getByLabelText('起点前移一帧'));
    fireEvent.click(screen.getByLabelText('终点后移一帧'));
    expect(onNudge).toHaveBeenNthCalledWith(1, 'wm-1', 1, 'start', -1);
    expect(onNudge).toHaveBeenNthCalledWith(2, 'wm-1', 1, 'end', 1);
  });

  it('seeks when the playhead slider moves', () => {
    const onSeek = vi.fn();
    renderTimeline({ onSeek });
    fireEvent.change(screen.getByLabelText('播放头'), { target: { value: '42' } });
    expect(onSeek).toHaveBeenCalledWith(42);
  });

  it('keeps every frame addressable even on a one-frame interval', () => {
    renderTimeline({ tracks: [track([[7, 7]])] });
    // 1 帧块也不能塌成 0 宽：最小宽度由 CSS 的 min-width 保证，位置仍是比例。
    expect(screen.getByTestId('block-wm-1-0').style.left).toBe('7%');
  });
});

describe('empty state', () => {
  it('renders no lanes when there is nothing to show', () => {
    renderTimeline({ tracks: [] });
    expect(screen.queryAllByTestId(/^lane-/)).toHaveLength(0);
    expect(screen.getByLabelText('播放头')).toBeTruthy();
  });
});
