/**
 * Mapping between what the user sees and video pixels.
 *
 * The preview uses `object-fit: contain`, so the picture is letterboxed inside
 * the element. Every selection therefore needs the same two conversions, and
 * getting them subtly wrong is how a mask ends up offset by the letterbox —
 * which looks like "the eraser is bad at its job" instead of "the maths is
 * wrong". Keeping them pure and tested is cheaper than debugging that later.
 */

import type { Rect } from '../../shared/ipc';

export interface Size {
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

/** Where the video picture actually sits inside the element, in element pixels. */
export interface ContentBox {
  left: number;
  top: number;
  width: number;
  height: number;
  scale: number;
}

export function contentBox(element: Size, video: Size): ContentBox {
  if (element.width <= 0 || element.height <= 0 || video.width <= 0 || video.height <= 0) {
    return { left: 0, top: 0, width: 0, height: 0, scale: 1 };
  }
  const scale = Math.min(element.width / video.width, element.height / video.height);
  const width = video.width * scale;
  const height = video.height * scale;
  return {
    left: (element.width - width) / 2,
    top: (element.height - height) / 2,
    width,
    height,
    scale,
  };
}

/** Element coordinates -> video pixel coordinates, clamped to the frame. */
export function elementToVideo(point: Point, element: Size, video: Size): Point {
  const box = contentBox(element, video);
  const x = (point.x - box.left) / box.scale;
  const y = (point.y - box.top) / box.scale;
  return {
    x: clamp(Math.round(x), 0, video.width),
    y: clamp(Math.round(y), 0, video.height),
  };
}

/** Video pixel rectangle -> element coordinates, for drawing the overlay. */
export function videoToElement(rect: Rect, element: Size, video: Size): ContentBox {
  const box = contentBox(element, video);
  return {
    left: box.left + rect.x * box.scale,
    top: box.top + rect.y * box.scale,
    width: rect.width * box.scale,
    height: rect.height * box.scale,
    scale: box.scale,
  };
}

/** A rect from two drag corners, in video pixels, with a minimum size of 2px. */
export function rectFromDrag(start: Point, end: Point, video: Size): Rect {
  const left = Math.min(start.x, end.x);
  const top = Math.min(start.y, end.y);
  const right = Math.max(start.x, end.x);
  const bottom = Math.max(start.y, end.y);
  const x = clamp(Math.round(left), 0, Math.max(0, video.width - 1));
  const y = clamp(Math.round(top), 0, Math.max(0, video.height - 1));
  const width = clamp(Math.round(right) - x, 2, video.width - x);
  const height = clamp(Math.round(bottom) - y, 2, video.height - y);
  return { x, y, width, height };
}

/** Keep a rect inside the frame (used after the video is replaced). */
export function clampRect(rect: Rect, video: Size): Rect {
  const width = clamp(Math.round(rect.width), 2, video.width);
  const height = clamp(Math.round(rect.height), 2, video.height);
  const x = clamp(Math.round(rect.x), 0, Math.max(0, video.width - width));
  const y = clamp(Math.round(rect.y), 0, Math.max(0, video.height - height));
  return { x, y, width, height };
}

export function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '--:--';
  }
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const pad = (value: number): string => String(value).padStart(2, '0');
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${minutes}:${pad(rest)}`;
}
