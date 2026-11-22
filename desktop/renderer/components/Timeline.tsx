// 时间线：每条轨道一行泳道，区间是块，切点是竖线，播放头可拖动。
// 位置一律用百分比，不依赖元素测量，因此在 jsdom 里也能断言。
import { useRef } from 'react';
import { useI18n } from '../i18n/useI18n';
import type { FrameInterval, WatermarkTrack } from '../../shared/ipc';
import type { Selection } from '../store/analysis';
import type { Edge } from '../store/analysisOps';

interface TimelineProps {
  tracks: WatermarkTrack[];
  shots: FrameInterval[];
  frameCount: number;
  fps: number;
  currentFrame: number;
  selected: Selection | null;
  onSelect: (trackId: string, intervalIndex: number) => void;
  onSeek: (frame: number) => void;
  onNudge: (trackId: string, intervalIndex: number, edge: Edge, delta: number) => void;
}

export function Timeline({
  tracks,
  shots,
  frameCount,
  fps,
  currentFrame,
  selected,
  onSelect,
  onSeek,
  onNudge,
}: TimelineProps) {
  const { t } = useI18n();
  const laneRef = useRef<HTMLDivElement>(null);
  const total = Math.max(1, frameCount);
  const last = Math.max(0, total - 1);

  const percent = (frame: number): string => `${(frame / total) * 100}%`;
  const span = (frames: number): string => `${(Math.max(1, frames) / total) * 100}%`;

  // 拖动一个边界：像素差按泳道宽度换算成帧，边界本身由 store 重新钳制。
  const startDrag = (
    event: React.PointerEvent<HTMLElement>,
    trackId: string,
    intervalIndex: number,
    edge: Edge,
  ): void => {
    const width = laneRef.current?.getBoundingClientRect().width ?? 0;
    if (width <= 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const originX = event.clientX;
    let applied = 0;
    const move = (moveEvent: PointerEvent): void => {
      const delta = Math.round(((moveEvent.clientX - originX) / width) * total) - applied;
      if (delta !== 0) {
        applied += delta;
        onNudge(trackId, intervalIndex, edge, delta);
      }
    };
    const stop = (): void => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  };

  return (
    <div className="timeline">
      <label className="timeline__scrub">
        <span className="visually-hidden">{t('timeline.playhead')}</span>
        <input
          type="range"
          aria-label={t('timeline.playhead')}
          min={0}
          max={last}
          step={1}
          value={Math.min(last, Math.max(0, currentFrame))}
          onChange={(event) => onSeek(Number(event.target.value))}
        />
      </label>

      <div className="timeline__stack">
        <div className="timeline__overlay" aria-hidden="true">
          {shots.slice(1).map((shot, index) => (
            <span
              key={`${shot.start_frame}-${index}`}
              data-testid={`cut-${index}`}
              className="timeline__cut"
              style={{ left: percent(shot.start_frame) }}
            />
          ))}
          <span
            data-testid="playhead"
            className="timeline__playhead"
            style={{ left: percent(currentFrame) }}
          />
        </div>

        {tracks.map((track) => (
          <div className="timeline__lane" data-testid={`lane-${track.id}`} key={track.id} ref={laneRef}>
            {track.intervals.map((interval, index) => {
              const isSelected =
                selected?.trackId === track.id && selected.intervalIndex === index;
              return (
                <div
                  key={`${track.id}-${index}`}
                  data-testid={`block-${track.id}-${index}`}
                  role="button"
                  tabIndex={0}
                  aria-label={`${t('timeline.watermark')} ${index + 1}`}
                  aria-pressed={isSelected}
                  className={`timeline__block${isSelected ? ' is-selected' : ''}`}
                  style={{
                    left: percent(interval.start_frame),
                    width: span(interval.end_frame - interval.start_frame + 1),
                  }}
                  onClick={() => onSelect(track.id, index)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onSelect(track.id, index);
                    }
                  }}
                >
                  <span
                    className="timeline__handle timeline__handle--start"
                    onPointerDown={(event) => startDrag(event, track.id, index, 'start')}
                  />
                  <span
                    className="timeline__handle timeline__handle--end"
                    onPointerDown={(event) => startDrag(event, track.id, index, 'end')}
                  />
                  {isSelected ? (
                    <span className="timeline__nudges">
                      <button
                        type="button"
                        aria-label={t('timeline.startEarlier')}
                        onClick={(event) => {
                          event.stopPropagation();
                          onNudge(track.id, index, 'start', -1);
                        }}
                      >
                        ‹
                      </button>
                      <button
                        type="button"
                        aria-label={t('timeline.startLater')}
                        onClick={(event) => {
                          event.stopPropagation();
                          onNudge(track.id, index, 'start', 1);
                        }}
                      >
                        ›
                      </button>
                      <button
                        type="button"
                        aria-label={t('timeline.endEarlier')}
                        onClick={(event) => {
                          event.stopPropagation();
                          onNudge(track.id, index, 'end', -1);
                        }}
                      >
                        ‹
                      </button>
                      <button
                        type="button"
                        aria-label={t('timeline.endLater')}
                        onClick={(event) => {
                          event.stopPropagation();
                          onNudge(track.id, index, 'end', 1);
                        }}
                      >
                        ›
                      </button>
                    </span>
                  ) : null}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <div className="timeline__scale">
        {Array.from({ length: 6 }, (_item, index) => {
          const frame = Math.round((last * index) / 5);
          return (
            <span key={index} className="timeline__tick">
              {(frame / Math.max(1, fps)).toFixed(1)}s
            </span>
          );
        })}
      </div>
    </div>
  );
}
