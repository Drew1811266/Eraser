/**
 * Shared vocabulary for the main process, the preload bridge and the renderer.
 *
 * These mirror the engine contract (`contract/methods.json`) rather than
 * importing the generated module, because the renderer must not depend on
 * engine internals — it only ever sees what the main process chooses to forward.
 */

export interface DeviceInfo {
  kind: 'cuda' | 'cpu' | string;
  name: string;
  memory_mb?: number;
  provider?: string;
  /** False when the hardware exists but the engine cannot actually use it. */
  available?: boolean;
}

export interface RuntimeInfo {
  available_providers: string[];
  cuda_usable: boolean;
  onnxruntime: string | null;
}

export interface Capabilities {
  engine_version: string;
  python_version: string;
  platform: string;
  methods: string[];
  devices: DeviceInfo[];
  runtime: RuntimeInfo;
}

export interface ProbeResult {
  path: string;
  width: number;
  height: number;
  fps: number;
  frame_count: number;
  duration_seconds: number;
  has_audio: boolean;
  video_codec: string;
  container: string;
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Segment {
  id?: string;
  start_frame: number;
  end_frame: number;
  rect: Rect;
  expand_px?: number;
  feather_px?: number;
  enabled?: boolean;
}

export type TaskState = 'queued' | 'running' | 'success' | 'failed' | 'cancelled';

export interface TaskStatus {
  task_id: string;
  state: TaskState | string;
  phase: string;
  progress: number;
  processed: number;
  total: number;
  eta_seconds: number | null;
  message: string;
  /** Whatever the finished task produced; `output_path` is always present. */
  result?: {
    output_path: string;
    requested_model_id?: string;
    effective_model_id?: string;
    model_warning?: string;
    width?: number;
    height?: number;
    scale?: number;
  };
  error?: string;
}

export interface StartRequest {
  input_path: string;
  output_dir?: string;
  model_id?: string;
  segments: Segment[];
}

export interface EngineState {
  status: 'starting' | 'ready' | 'failed' | 'stopped';
  detail?: string;
  capabilities?: Capabilities;
}

export interface UpscaleRequest {
  input_path: string;
  output_dir?: string;
  scale?: 2 | 4;
}

export interface PickResult {
  canceled: boolean;
  path?: string;
}

export const MEDIA_SCHEME = 'eraser-media';

/**
 * Address a local file the renderer is allowed to play.
 *
 * `file://` cannot be loaded from the dev server's http origin, and relaxing
 * webSecurity to work around that would hand every page in the app the whole
 * filesystem. A custom scheme served by the main process exposes exactly the
 * files the user picked and nothing else.
 */
export function mediaUrl(absolutePath: string): string {
  return `${MEDIA_SCHEME}://local/${encodeURIComponent(absolutePath)}`;
}

/**
 * The method surface the ported reference frontend expects on `window.wmr`.
 *
 * Kept as data rather than 31 hand-written wrappers: the preload turns each name
 * into a tunnel to `wmr:call`, and the main process implements them in one
 * switch (`electron/wmr.ts`).
 */
export const WMR_METHODS = [
  'select_file', 'select_folder', 'begin_select_file', 'begin_select_folder',
  'poll_dialog_result', 'clear_dialog_result',
  'get_media_info', 'prepare_video_preview',
  'open_video_preview_session', 'read_video_preview_frame', 'close_video_preview_session',
  'load_annotations', 'save_annotations', 'delete_annotations',
  'get_settings', 'save_settings', 'get_device_info',
  'process_video', 'stop_processing', 'open_output_dir',
  'get_model_download_status', 'start_model_download', 'cancel_model_download',
  'get_upscale_model_download_status', 'start_upscale_model_download', 'cancel_upscale_model_download',
  'get_upscale_capabilities', 'start_upscale', 'get_upscale_task_status', 'cancel_upscale_task',
] as const;

export type WmrMethod = (typeof WMR_METHODS)[number];

/** Everything the preload script puts on `window.eraser`. */export interface WmrBridge {
  [method: string]: (payload?: unknown) => Promise<unknown>;
}

export interface EraserBridge {
  capabilities(): Promise<Capabilities>;
  pickVideo(): Promise<PickResult>;
  pickOutputDir(): Promise<PickResult>;
  probe(path: string): Promise<ProbeResult>;
  start(request: StartRequest): Promise<{ task_id: string }>;
  upscale(request: UpscaleRequest): Promise<{ task_id: string }>;
  cancel(taskId: string): Promise<{ ok: boolean }>;
  status(taskId: string): Promise<TaskStatus>;
  reveal(path: string): Promise<void>;
  openExternal(path: string): Promise<void>;
  defaultOutputDir(): Promise<string>;
  onProgress(listener: (status: TaskStatus) => void): () => void;
  onEngineState(listener: (state: EngineState) => void): () => void;
  onEngineLog(listener: (line: string) => void): () => void;
}

export const IPC = {
  capabilities: 'app:capabilities',
  pickVideo: 'dialog:pick-video',
  pickOutputDir: 'dialog:pick-output-dir',
  probe: 'engine:probe',
  start: 'engine:start',
  upscale: 'engine:upscale',
  cancel: 'engine:cancel',
  status: 'engine:status',
  reveal: 'shell:reveal',
  openExternal: 'shell:open-external',
  defaultOutputDir: 'app:default-output-dir',
  wmrCall: 'wmr:call',
  progress: 'task:progress',
  engineState: 'engine:state',
  engineLog: 'engine:log',
} as const;
