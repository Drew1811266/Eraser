import { describe, expect, it } from 'vitest';
import {
  clampRect,
  contentBox,
  elementToVideo,
  formatDuration,
  rectFromDrag,
  videoToElement,
} from './geometry';

const video = { width: 1920, height: 1080 };

describe('contentBox', () => {
  it('letterboxes a 16:9 video inside a taller element', () => {
    const box = contentBox({ width: 1200, height: 900 }, video);
    expect(box.scale).toBeCloseTo(0.625);
    expect(box.width).toBeCloseTo(1200);
    expect(box.height).toBeCloseTo(675);
    expect(box.left).toBeCloseTo(0);
    expect(box.top).toBeCloseTo(112.5);
  });

  it('pillarboxes a 16:9 video inside a wider element', () => {
    const box = contentBox({ width: 1600, height: 400 }, video);
    expect(box.height).toBeCloseTo(400);
    expect(box.width).toBeCloseTo(711.111, 2);
    expect(box.left).toBeCloseTo(444.444, 2);
    expect(box.top).toBeCloseTo(0);
  });

  it('degrades safely before layout has happened', () => {
    expect(contentBox({ width: 0, height: 0 }, video).scale).toBe(1);
  });
});

describe('elementToVideo', () => {
  const element = { width: 1200, height: 900 };

  it('maps the picture origin, not the element origin', () => {
    const point = elementToVideo({ x: 0, y: 112.5 }, element, video);
    expect(point).toEqual({ x: 0, y: 0 });
  });

  it('maps the picture centre to the frame centre', () => {
    const point = elementToVideo({ x: 600, y: 450 }, element, video);
    expect(point).toEqual({ x: 960, y: 540 });
  });

  it('clamps clicks on the letterbox to the frame edge', () => {
    expect(elementToVideo({ x: 600, y: 0 }, element, video)).toEqual({ x: 960, y: 0 });
    expect(elementToVideo({ x: -50, y: 450 }, element, video)).toEqual({ x: 0, y: 540 });
  });
});

describe('videoToElement', () => {
  it('round-trips with elementToVideo', () => {
    const element = { width: 800, height: 800 };
    const drawn = videoToElement({ x: 100, y: 200, width: 300, height: 150 }, element, video);
    const back = elementToVideo(
      { x: drawn.left + drawn.width / 2, y: drawn.top + drawn.height / 2 },
      element,
      video,
    );
    expect(back.x).toBeCloseTo(250, 0);
    expect(back.y).toBeCloseTo(275, 0);
  });
});

describe('rectFromDrag', () => {
  it('normalises a drag that goes up and to the left', () => {
    const rect = rectFromDrag({ x: 500, y: 400 }, { x: 200, y: 100 }, video);
    expect(rect).toEqual({ x: 200, y: 100, width: 300, height: 300 });
  });

  it('never produces a zero-sized rect from a click', () => {
    const rect = rectFromDrag({ x: 10, y: 10 }, { x: 10, y: 10 }, video);
    expect(rect.width).toBeGreaterThanOrEqual(2);
    expect(rect.height).toBeGreaterThanOrEqual(2);
  });

  it('stops at the frame edge', () => {
    const rect = rectFromDrag({ x: 1900, y: 1000 }, { x: 3000, y: 3000 }, video);
    expect(rect.x + rect.width).toBeLessThanOrEqual(video.width);
    expect(rect.y + rect.height).toBeLessThanOrEqual(video.height);
  });
});

describe('clampRect', () => {
  it('pulls an out-of-bounds rect back inside', () => {
    const rect = clampRect({ x: -40, y: -40, width: 400, height: 300 }, video);
    expect(rect).toEqual({ x: 0, y: 0, width: 400, height: 300 });
  });

  it('shrinks a rect bigger than the frame', () => {
    const rect = clampRect({ x: 0, y: 0, width: 5000, height: 5000 }, video);
    expect(rect.width).toBe(video.width);
    expect(rect.height).toBe(video.height);
  });
});

describe('formatDuration', () => {
  it('formats minutes and hours', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(65)).toBe('1:05');
    expect(formatDuration(3725)).toBe('1:02:05');
  });

  it('rejects nonsense', () => {
    expect(formatDuration(Number.NaN)).toBe('--:--');
    expect(formatDuration(-5)).toBe('--:--');
  });
});
