import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { mediaUrl, type Capabilities, type EngineState, type ProbeResult, type Rect, type TaskStatus } from '../shared/ipc';
import { VideoStage } from './components/VideoStage';
import { formatDuration } from './lib/geometry';

type Phase = 'idle' | 'ready' | 'running' | 'done';

const MODEL_OPTIONS = [{ id: 'lama_roi', label: 'LaMa ROI（推荐）' }];

export function App(): ReactElement {
  const [engine, setEngine] = useState<EngineState>({ status: 'starting' });
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [filePath, setFilePath] = useState<string | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [outputDir, setOutputDir] = useState('');
  const [modelId, setModelId] = useState(MODEL_OPTIONS[0]!.id);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatus | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [showLogs, setShowLogs] = useState(false);
  const [recentOutput, setRecentOutput] = useState<string>('');
  const logRef = useRef<string[]>([]);

  const phase: Phase = status?.state === 'success' || status?.state === 'failed'
    ? 'done'
    : taskId
      ? 'running'
      : probe
        ? 'ready'
        : 'idle';

  useEffect(() => {
    window.eraser
      .capabilities()
      .then((caps) => {
        setCapabilities(caps);
        setEngine({ status: 'ready', capabilities: caps });
      })
      .catch((cause: Error) => setEngine({ status: 'failed', detail: cause.message }));

    const unsubscribeState = window.eraser.onEngineState((state) => {
      setEngine(state);
      if (state.capabilities) {
        setCapabilities(state.capabilities);
      }
    });
    const unsubscribeProgress = window.eraser.onProgress((next) => {
      setStatus((current) => (current && current.task_id !== next.task_id ? current : next));
    });
    const unsubscribeLog = window.eraser.onEngineLog((line) => {
      logRef.current = [...logRef.current.slice(-199), line];
      setLogs(logRef.current);
    });

    void window.eraser.defaultOutputDir().then(setOutputDir);

    return () => {
      unsubscribeState();
      unsubscribeProgress();
      unsubscribeLog();
    };
  }, []);

  // The shell reports progress through notifications *and* keeps a status the
  // renderer can poll; a dropped notification must not freeze the bar.
  useEffect(() => {
    if (!taskId || phase === 'done') {
      return;
    }
    const timer = window.setInterval(() => {
      window.eraser
        .status(taskId)
        .then((next) => setStatus(next))
        .catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [taskId, phase]);

  const openVideo = useCallback(async () => {
    setError('');
    setBusy(true);
    try {
      const picked = await window.eraser.pickVideo();
      if (picked.canceled || !picked.path) {
        return;
      }
      const info = await window.eraser.probe(picked.path);
      setFilePath(picked.path);
      setProbe(info);
      setStatus(null);
      setTaskId(null);
      setRect(suggestRect(info));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(async () => {
    if (!filePath || !probe || !rect) {
      return;
    }
    setError('');
    setStatus(null);
    setRecentOutput('');
    try {
      const started = await window.eraser.start({
        input_path: filePath,
        output_dir: outputDir || undefined,
        model_id: modelId,
        segments: [
          {
            start_frame: 0,
            end_frame: Math.max(0, probe.frame_count - 1),
            rect,
            expand_px: 5,
            feather_px: 3,
            enabled: true,
          },
        ],
      });
      setTaskId(started.task_id);
      setStatus({
        task_id: started.task_id,
        state: 'running',
        phase: 'prepare',
        progress: 0,
        processed: 0,
        total: probe.frame_count,
        eta_seconds: null,
        message: '准备中…',
      });
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
    }
  }, [filePath, probe, rect, outputDir, modelId]);

  const cancel = useCallback(async () => {
    if (!taskId) {
      return;
    }
    try {
      await window.eraser.cancel(taskId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [taskId]);

  useEffect(() => {
    if (status?.state === 'success' && status.result?.output_path) {
      setRecentOutput(status.result.output_path);
    }
  }, [status?.state, status?.result?.output_path]);

  const pickOutputDir = useCallback(async () => {
    const picked = await window.eraser.pickOutputDir();
    if (!picked.canceled && picked.path) {
      setOutputDir(picked.path);
    }
  }, []);

  const gpu = useMemo(() => {
    const device = capabilities?.devices.find((item) => item.kind === 'cuda' && item.available);
    return device?.name ?? null;
  }, [capabilities]);

  const percent = Math.round(((status?.progress ?? 0) * 1000)) / 10;
  const running = phase === 'running' && status?.state !== 'failed' && status?.state !== 'cancelled';

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">⌫</span>
          <span>Eraser</span>
          <span className="brand-sub">视频水印擦除</span>
        </div>
        <div className="topbar-actions">
          <button className="ghost" onClick={() => setShowLogs((value) => !value)}>
            {showLogs ? '隐藏日志' : '日志'}
          </button>
          <span className={`engine-badge ${engine.status}`}>
            {engine.status === 'ready' ? `引擎就绪 · ${gpu ?? 'CPU'}` : engineLabel(engine)}
          </span>
        </div>
      </header>

      <main className="workspace">
        <section className="canvas">
          <VideoStage
            src={filePath ? mediaUrl(filePath) : null}
            video={probe ? { width: probe.width, height: probe.height } : { width: 16, height: 9 }}
            rect={rect}
            disabled={running}
            onRectChange={setRect}
          />
          {probe && (
            <div className="meta-row">
              <span title={filePath ?? ''}>{baseName(filePath)}</span>
              <span className="dot" />
              <span>{probe.width} × {probe.height}</span>
              <span className="dot" />
              <span>{probe.fps.toFixed(2)} fps</span>
              <span className="dot" />
              <span>{probe.frame_count} 帧 · {formatDuration(probe.duration_seconds)}</span>
              <span className="dot" />
              <span>{probe.video_codec || '未知编码'}</span>
            </div>
          )}
        </section>

        <aside className="panel">
          <div className="panel-block">
            <h2>1 · 选择视频</h2>
            <button className="primary" onClick={openVideo} disabled={busy || running}>
              {probe ? '重新选择' : '打开视频'}
            </button>
          </div>

          <div className="panel-block">
            <h2>2 · 框选水印</h2>
            {rect ? (
              <div className="grid2">
                <Field label="X" value={rect.x} onChange={(value) => setRect({ ...rect, x: value })} />
                <Field label="Y" value={rect.y} onChange={(value) => setRect({ ...rect, y: value })} />
                <Field label="宽" value={rect.width} onChange={(value) => setRect({ ...rect, width: value })} />
                <Field label="高" value={rect.height} onChange={(value) => setRect({ ...rect, height: value })} />
              </div>
            ) : (
              <p className="muted">在预览区拖动鼠标，框住要去掉的水印。</p>
            )}
          </div>

          <div className="panel-block">
            <h2>3 · 输出</h2>
            <label className="field-label">模型</label>
            <select value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={running}>
              {MODEL_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
            <label className="field-label">保存到</label>
            <div className="row">
              <input value={outputDir} onChange={(event) => setOutputDir(event.target.value)} disabled={running} />
              <button className="ghost" onClick={pickOutputDir} disabled={running}>选择</button>
            </div>
          </div>

          <div className="panel-block grow">
            {running ? (
              <>
                <div className="progress-head">
                  <span>{status?.message || '处理中…'}</span>
                  <span>{percent.toFixed(1)}%</span>
                </div>
                <div className="progress">
                  <div className="progress-fill" style={{ width: `${percent}%` }} />
                </div>
                <div className="progress-foot">
                  <span>
                    {status?.processed ?? 0} / {status?.total ?? probe?.frame_count ?? 0} 帧
                  </span>
                  <span>{formatEta(status?.eta_seconds ?? null)}</span>
                </div>
                <button className="danger" onClick={cancel}>取消</button>
              </>
            ) : (
              <>
                <button
                  className="primary big"
                  onClick={start}
                  disabled={!probe || !rect || engine.status !== 'ready' || busy}
                >
                  开始擦除
                </button>
                {phase === 'done' && status?.state === 'success' && recentOutput && (
                  <div className="result">
                    <p className="ok">完成</p>
                    <p className="path" title={recentOutput}>{recentOutput}</p>
                    <div className="row">
                      <button className="ghost" onClick={() => window.eraser.openExternal(recentOutput)}>播放</button>
                      <button className="ghost" onClick={() => window.eraser.reveal(recentOutput)}>打开所在文件夹</button>
                    </div>
                  </div>
                )}
                {status?.state === 'failed' && (
                  <p className="error">{status.error || '处理失败'}</p>
                )}
                {status?.state === 'cancelled' && <p className="muted">已取消</p>}
              </>
            )}
            {error && <p className="error">{error}</p>}
          </div>
        </aside>
      </main>

      {showLogs && (
        <footer className="logs">
          <div className="logs-head">
            <span>引擎日志</span>
            <button className="ghost tiny" onClick={() => { logRef.current = []; setLogs([]); }}>清空</button>
          </div>
          <pre>{logs.length ? logs.join('\n') : '（暂无）'}</pre>
        </footer>
      )}
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }): ReactElement {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        onChange={(event) => onChange(Number(event.target.value) || 0)}
      />
    </label>
  );
}

function engineLabel(state: EngineState): string {
  if (state.status === 'starting') {
    return '引擎启动中…';
  }
  if (state.status === 'failed') {
    return `引擎异常${state.detail ? `：${state.detail}` : ''}`;
  }
  return '引擎未就绪';
}

function baseName(path: string | null): string {
  if (!path) {
    return '';
  }
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] ?? path;
}

function formatEta(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0) {
    return '预计剩余 --:--';
  }
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `预计剩余 ${minutes}:${String(rest).padStart(2, '0')}`;
}

/** A sensible first guess: the top-right corner, where watermarks usually sit. */
function suggestRect(info: ProbeResult): Rect {
  const width = Math.max(24, Math.round(info.width * 0.22));
  const height = Math.max(16, Math.round(info.height * 0.12));
  return {
    x: Math.max(0, info.width - width - Math.round(info.width * 0.02)),
    y: Math.max(0, Math.round(info.height * 0.02)),
    width,
    height,
  };
}
