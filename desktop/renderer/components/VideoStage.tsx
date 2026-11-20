import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react';
import type { Rect } from '../../shared/ipc';
import { contentBox, elementToVideo, rectFromDrag, videoToElement, type Point } from '../lib/geometry';

type Handle = 'nw' | 'ne' | 'sw' | 'se' | 'move';

interface DragState {
  handle: Handle | 'draw';
  start: Point;
  origin: Rect | null;
}

interface Props {
  src: string | null;
  video: { width: number; height: number };
  rect: Rect | null;
  disabled?: boolean;
  onRectChange: (rect: Rect | null) => void;
}

const MIN_SIZE = 8;

/**
 * The preview plus the watermark selection.
 *
 * All geometry is stored in *video pixels*; the element only ever converts for
 * display. That means changing the window size, or swapping in a different
 * resolution video, never silently moves the mask the user drew.
 */
export function VideoStage({ src, video, rect, disabled = false, onRectChange }: Props): ReactElement {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [elementSize, setElementSize] = useState({ width: 0, height: 0 });
  const dragRef = useRef<DragState | null>(null);

  useEffect(() => {
    const element = stageRef.current;
    if (!element) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      const { width, height } = entry.contentRect;
      setElementSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const toVideoPoint = useCallback(
    (event: React.PointerEvent): Point => {
      const element = stageRef.current;
      if (!element) {
        return { x: 0, y: 0 };
      }
      const bounds = element.getBoundingClientRect();
      return elementToVideo(
        { x: event.clientX - bounds.left, y: event.clientY - bounds.top },
        { width: bounds.width, height: bounds.height },
        video,
      );
    },
    [video],
  );

  const beginDrag = (event: React.PointerEvent, handle: Handle | 'draw'): void => {
    if (disabled || !src) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    (event.target as Element).setPointerCapture?.(event.pointerId);
    const point = toVideoPoint(event);
    dragRef.current = {
      handle,
      start: point,
      origin: handle === 'move' ? rect : null,
    };
    if (handle === 'draw') {
      onRectChange(rectFromDrag(point, point, video));
    }
  };

  const onPointerMove = (event: React.PointerEvent): void => {
    const drag = dragRef.current;
    if (!drag) {
      return;
    }
    const point = toVideoPoint(event);

    if (drag.handle === 'draw') {
      onRectChange(rectFromDrag(drag.start, point, video));
      return;
    }
    if (!drag.origin) {
      return;
    }
    if (drag.handle === 'move') {
      const dx = point.x - drag.start.x;
      const dy = point.y - drag.start.y;
      onRectChange(clampToFrame({
        x: drag.origin.x + dx,
        y: drag.origin.y + dy,
        width: drag.origin.width,
        height: drag.origin.height,
      }, video));
      return;
    }

    const anchor = oppositeCorner(drag.origin, drag.handle);
    const resized = rectFromDrag(anchor, point, video);
    onRectChange({
      x: resized.x,
      y: resized.y,
      width: Math.max(MIN_SIZE, resized.width),
      height: Math.max(MIN_SIZE, resized.height),
    });
  };

  const endDrag = (event: React.PointerEvent): void => {
    if (!dragRef.current) {
      return;
    }
    dragRef.current = null;
    (event.target as Element).releasePointerCapture?.(event.pointerId);
  };

  const box = contentBox(elementSize, video);
  const overlay = rect ? videoToElement(rect, elementSize, video) : null;

  return (
    <div className="stage" ref={stageRef}>
      {src ? (
        <video className="stage-video" src={src} controls={false} playsInline muted preload="auto" />
      ) : (
        <div className="stage-empty">
          <div className="stage-empty-icon">▦</div>
          <p>打开一个视频开始</p>
          <p className="muted">拖拽框选水印区域，然后开始处理</p>
        </div>
      )}

      {src && (
        <div
          className="stage-overlay"
          onPointerDown={(event) => beginDrag(event, 'draw')}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          style={{ cursor: disabled ? 'default' : 'crosshair' }}
        >
          {/* The video picture area, so the user can see where the frame ends. */}
          <div
            className="stage-frame"
            style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
          />
          {overlay && (
            <div
              className="selection"
              style={{ left: overlay.left, top: overlay.top, width: overlay.width, height: overlay.height }}
              onPointerDown={(event) => beginDrag(event, 'move')}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
            >
              {(['nw', 'ne', 'sw', 'se'] as const).map((handle) => (
                <span
                  key={handle}
                  className={`handle handle-${handle}`}
                  onPointerDown={(event) => beginDrag(event, handle)}
                  onPointerMove={onPointerMove}
                  onPointerUp={endDrag}
                />
              ))}
              <span className="selection-label">水印</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function oppositeCorner(rect: Rect, handle: Handle): Point {
  switch (handle) {
    case 'nw':
      return { x: rect.x + rect.width, y: rect.y + rect.height };
    case 'ne':
      return { x: rect.x, y: rect.y + rect.height };
    case 'sw':
      return { x: rect.x + rect.width, y: rect.y };
    case 'se':
      return { x: rect.x, y: rect.y };
    default:
      return { x: rect.x, y: rect.y };
  }
}

function clampToFrame(rect: Rect, video: { width: number; height: number }): Rect {
  const x = Math.min(Math.max(0, rect.x), Math.max(0, video.width - rect.width));
  const y = Math.min(Math.max(0, rect.y), Math.max(0, video.height - rect.height));
  return { x, y, width: Math.min(rect.width, video.width), height: Math.min(rect.height, video.height) };
}
