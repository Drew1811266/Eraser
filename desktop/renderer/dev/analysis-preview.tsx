// 开发用视觉验收页：只挂载分析视图，用假的桌面桥喂一份固定结果。
//
// 存在的理由：jsdom 不应用样式表，Electron 又不一定能在自动化会话里启动。
// 这里用 Vite 开发服务器 + 真实 Chromium，能看到真实的布局、块宽和配色。
//
//   cd desktop && npm run dev:renderer
//   打开 http://localhost:5273/dev/analysis-preview.html
import { createRoot } from 'react-dom/client';
import '../styles.css';
import { AnalysisView } from '../views/AnalysisView';
import { useAnalysisStore } from '../store/analysis';
import type { Analysis } from '../../shared/ipc';

const VIDEO = 'C:/fixtures/corner_static.mp4';

// 640×360 的假画面，带一个和夹具同位置的水印块。
const FRAME =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#203040"/><stop offset="1" stop-color="#806040"/>
      </linearGradient></defs>
      <rect width="640" height="360" fill="url(#g)"/>
      <text x="374" y="336" font-family="Segoe UI" font-size="26" fill="#ffffff" opacity="0.85">© StudioName 2026</text>
    </svg>`,
  );

const analysis: Analysis = {
  video: { width: 640, height: 360, fps: 10, frame_count: 100, duration_seconds: 10 },
  shots: [
    { start_frame: 0, end_frame: 32 },
    { start_frame: 33, end_frame: 65 },
    { start_frame: 66, end_frame: 99 },
  ],
  watermarks: [
    {
      id: 'wm-1',
      intervals: [
        { start_frame: 0, end_frame: 48 },
        { start_frame: 52, end_frame: 99 },
      ],
      keyframes: [
        { frame: 0, rect: { x: 374, y: 314, width: 246, height: 30 } },
        { frame: 52, rect: { x: 374, y: 314, width: 246, height: 30 } },
      ],
      motion: 'static',
      confidence: 1,
    },
  ],
  suggested: {
    engine_id: 'lama_roi',
    split_by_shot: true,
    warnings: ['multiple_watermarks'],
  },
  model_id: 'florence2',
  elapsed_ms: 6400,
};

const bridge = {
  analyzeCapabilities: async () => ({
    detectors: [
      {
        model_id: 'florence2',
        display_name: 'Florence-2 (watermark)',
        installed: true,
        available: true,
        blocked_reason: '',
      },
    ],
    available: true,
  }),
  analyzeStart: async () => ({ task_id: 'preview' }),
  status: async () => ({
    task_id: 'preview',
    state: 'success',
    phase: 'done',
    progress: 1,
    processed: 1,
    total: 1,
    eta_seconds: null,
    message: '',
    result: analysis,
  }),
  compileAnnotations: async () => ({ segments: [] }),
  installDetector: async () => ({ task_id: 'preview-install' }),
};

(window as unknown as { eraser: typeof bridge }).eraser = bridge;

// 预置结果，页面一打开就是"分析完成"的状态。
useAnalysisStore.getState().succeed(analysis, VIDEO);

createRoot(document.getElementById('root')!).render(
  <AnalysisView
    videoPath={VIDEO}
    videoMeta={{ width: 640, height: 360, frame_count: 100, fps: 10 }}
    frameImageUrl={FRAME}
    onStart={async () => undefined}
  />,
);
