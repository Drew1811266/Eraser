// 分析视图：查能力 → 装检测器 → 分析 → 时间线上确认 → 编译交给处理。
// 所有引擎调用都经过 window.eraser（主进程桥），渲染进程不直接碰引擎。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n';
import { Timeline } from '../components/Timeline';
import { AnalysisReport } from './AnalysisReport';
import { useAnalysisStore } from '../store/analysis';
import { useWorkspaceStore } from '../store/workspace';
import { analysisError } from './analysisErrors';
import type {
  Analysis,
  DetectorEntry,
  EraserBridge,
  Segment,
  TaskStatus,
} from '../../shared/ipc';

const POLL_MS = 250;
const TERMINAL = new Set(['success', 'failed', 'cancelled']);

function desktop(): EraserBridge {
  const api = window.eraser;
  if (!api) {
    throw new Error('桌面接口不可用');
  }
  return api;
}

async function waitFor(
  taskId: string,
  onTick?: (status: TaskStatus) => void,
  timeoutMs = 15 * 60_000,
): Promise<TaskStatus> {
  const api = desktop();
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const status = await api.status(taskId);
    onTick?.(status);
    if (TERMINAL.has(status.state)) {
      return status;
    }
    // 没有超时的轮询会让按钮永久禁用；一个卡住的任务必须变成一条错误。
    if (Date.now() > deadline) {
      throw new Error('任务超时，请重试');
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

interface AnalysisViewProps {
  videoPath: string;
  videoMeta: { width: number; height: number; frame_count: number; fps: number } | null;
  frameImageUrl: string;
  onStart: (segments: Segment[]) => Promise<void> | void;
}

export function AnalysisView({ videoPath, videoMeta, frameImageUrl, onStart }: AnalysisViewProps) {
  const { t } = useI18n();
  const state = useAnalysisStore();
  const [detectors, setDetectors] = useState<DetectorEntry[]>([]);
  const [compiling, setCompiling] = useState(false);
  const install = state.install;

  const detector = useMemo(
    () => detectors.find((entry) => entry.available) ?? detectors[0] ?? null,
    [detectors],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await desktop().analyzeCapabilities();
        if (!cancelled) {
          setDetectors(result.detectors ?? []);
        }
      } catch (error) {
        if (!cancelled) {
          state.fail(analysisError(error, t));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // 只在挂载时查一次；后续刷新走 refreshCapabilities。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startAnalysis = useCallback(async () => {
    // 同步进入 running，堵住"点击到任务返回"之间重复点击起两个任务的空隙。
    if (useAnalysisStore.getState().phase === 'running') {
      return;
    }
    useAnalysisStore.getState().begin('pending');
    try {
      const { task_id } = await desktop().analyzeStart({ input_path: videoPath });
      useAnalysisStore.getState().begin(task_id);
      const status = await waitFor(task_id, (tick) =>
        useAnalysisStore.getState().setProgress(tick.progress, tick.message),
      );
      if (status.state === 'success') {
        const payload = status.result as unknown as Analysis;
        if (!payload || !payload.video) {
          throw new Error('分析结果为空');
        }
        useAnalysisStore.getState().succeed(payload);
      } else {
        useAnalysisStore.getState().fail(status.error || status.state);
      }
    } catch (error) {
      useAnalysisStore.getState().fail(analysisError(error, t));
    }
  }, [videoPath, state]);

  const installDetector = useCallback(async (modelId: string) => {
    useAnalysisStore.getState().setInstall({ phase: 'running', progress: 0, message: '', error: '' });
    try {
      const { task_id } = await desktop().installDetector({ model_id: modelId });
      const status = await waitFor(task_id, (tick) =>
        useAnalysisStore.getState().setInstall({
          phase: 'running',
          progress: tick.progress,
          message: tick.message,
          error: '',
        }),
      );
      if (status.state !== 'success') {
        useAnalysisStore.getState().setInstall({
          phase: 'failed',
          progress: 0,
          message: '',
          error: analysisError(status.error || status.state, t),
        });
        return;
      }
      useAnalysisStore.getState().setInstall({ phase: 'success', progress: 1, message: '', error: '' });
      const result = await desktop().analyzeCapabilities();
      setDetectors(result.detectors ?? []);
    } catch (error) {
      useAnalysisStore.getState().setInstall({ phase: 'failed', progress: 0, message: '', error: analysisError(error, t) });
    }
  }, []);

  const startProcess = useCallback(async () => {
    const snapshot = useAnalysisStore.getState();
    const analysis = snapshot.analysis;
    if (!analysis) {
      return;
    }
    if (snapshot.videoPath && snapshot.videoPath !== videoPath) {
      useAnalysisStore.getState().fail('分析结果属于另一个视频，请重新分析');
      return;
    }
    setCompiling(true);
    try {
      const { segments } = await desktop().compileAnnotations({
        watermarks: useAnalysisStore.getState().tracks,
        shots: analysis.shots,
        video: {
          width: analysis.video.width,
          height: analysis.video.height,
          frame_count: analysis.video.frame_count,
        },
      });
      await onStart(segments);
    } catch (error) {
      useAnalysisStore.getState().fail(analysisError(error, t));
    } finally {
      setCompiling(false);
    }
  }, [onStart, videoPath]);

  const rerun = useCallback(async () => {
    // 重新分析会覆盖编辑过的轨道；有未编译的改动时先问一句。
    if (useAnalysisStore.getState().dirty) {
      const proceed =
        typeof window.confirm !== 'function' || window.confirm(t('analyze.confirmRerun'));
      if (!proceed) {
        return;
      }
    }
    await startAnalysis();
  }, [startAnalysis, t]);

  const analysis = state.analysis;
  const video = analysis?.video ?? videoMeta;
  const selectedTrack = state.selected
    ? state.tracks.find((track) => track.id === state.selected!.trackId)
    : undefined;
  const selectedKeyframe = selectedTrack?.keyframes.length
    ? selectedTrack.keyframes.reduce((best, keyframe) =>
        Math.abs(keyframe.frame - state.currentFrame) < Math.abs(best.frame - state.currentFrame)
          ? keyframe
          : best,
      )
    : null;

  const canSplit = Boolean(state.selected && analysis);
  const canMerge = Boolean(
    state.selected &&
      selectedTrack &&
      state.selected.intervalIndex < selectedTrack.intervals.length - 1,
  );

  return (
    <section className="analysis-view">
      <header className="analysis-view__bar">
        <h2>{t('analyze.header')}</h2>
        <button
          type="button"
          onClick={() => void rerun()}
          disabled={state.phase === 'running' || !detector?.available}
        >
          {analysis ? t('analyze.rerun') : t('analyze.run')}
        </button>
      </header>

      <AnalysisReport
        analysis={analysis}
        detector={detector}
        install={install}
        onInstall={(modelId) => void installDetector(modelId)}
      />

      {state.phase === 'failed' ? <p role="alert">{state.error}</p> : null}
      {state.phase === 'running' ? (
        <p>
          {t('analyze.running')} {Math.round(state.progress * 100)}%
        </p>
      ) : null}

      {analysis ? (
        <>
          <div className="analysis-stage">
            {frameImageUrl ? <img src={frameImageUrl} alt="" /> : null}
            {selectedKeyframe && video ? (
              <span
                className="analysis-stage__box"
                style={{
                  left: `${(selectedKeyframe.rect.x / video.width) * 100}%`,
                  top: `${(selectedKeyframe.rect.y / video.height) * 100}%`,
                  width: `${(selectedKeyframe.rect.width / video.width) * 100}%`,
                  height: `${(selectedKeyframe.rect.height / video.height) * 100}%`,
                }}
              />
            ) : null}
          </div>

          <Timeline
            tracks={state.tracks}
            shots={analysis.shots}
            frameCount={analysis.video.frame_count}
            fps={analysis.video.fps}
            currentFrame={state.currentFrame}
            selected={state.selected}
            onSelect={(trackId, intervalIndex) => state.select({ trackId, intervalIndex })}
            onSeek={(frame) => {
              state.setFrame(frame);
              // 预览抽帧跟随工作区的当前帧，所以两处必须同步。
              useWorkspaceStore.getState().setCurrentFrame(frame);
            }}
            onSetEdge={(trackId, intervalIndex, edge, frame) =>
              state.setEdge(trackId, intervalIndex, edge, frame)
            }
            onNudge={(trackId, intervalIndex, edge, delta) =>
              state.nudge(trackId, intervalIndex, edge, delta)
            }
          />

          <div className="analysis-view__tools">
            <button
              type="button"
              disabled={!canSplit}
              onClick={() =>
                state.selected &&
                state.split(state.selected.trackId, state.selected.intervalIndex, state.currentFrame)
              }
            >
              {t('analyze.split')}
            </button>
            <button
              type="button"
              disabled={!canMerge}
              onClick={() =>
                state.selected && state.merge(state.selected.trackId, state.selected.intervalIndex)
              }
            >
              {t('analyze.merge')}
            </button>
            <button
              type="button"
              disabled={!state.selected}
              onClick={() =>
                state.selected && state.remove(state.selected.trackId, state.selected.intervalIndex)
              }
            >
              {t('analyze.delete')}
            </button>
            <button
              type="button"
              onClick={() =>
                state.addManual(
                  selectedKeyframe?.rect ?? {
                    x: Math.round((video?.width ?? 640) * 0.4),
                    y: Math.round((video?.height ?? 360) * 0.4),
                    width: Math.round((video?.width ?? 640) * 0.2),
                    height: Math.round((video?.height ?? 360) * 0.1),
                  },
                  state.currentFrame,
                )
              }
            >
              {t('analyze.addManual')}
            </button>
            <button
              type="button"
              onClick={() => void startProcess()}
              disabled={compiling || state.tracks.length === 0}
            >
              {compiling ? t('analyze.compiling') : t('analyze.start')}
            </button>
          </div>
        </>
      ) : null}
    </section>
  );
}
