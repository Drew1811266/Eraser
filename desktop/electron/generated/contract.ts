// AUTO-GENERATED FROM contract/methods.json + contract/errors.json. DO NOT EDIT.

export const CONTRACT_VERSION = "1.0.0";

// ---- shared types ----
export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface VideoMeta {
  width: number;
  height: number;
  frame_count: number;
}

export interface Segment {
  id?: string;
  start_frame: number;
  end_frame: number;
  rect: Rect;
  expand_px?: number;
  feather_px?: number;
  enabled?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface MediaInfo {
  path: string;
  width: number;
  height: number;
  fps: number;
  frame_count: number;
  duration_seconds?: number;
  has_audio: boolean;
  video_codec?: string;
  container?: string;
}

export interface TaskStatus {
  task_id: string;
  state: string;
  phase: string;
  progress: number;
  processed?: number;
  total?: number;
  eta_seconds?: number | null;
  message?: string;
}

export interface ModelEntry {
  model_id: string;
  display_name: string;
  engine: string;
  installed: boolean;
  implemented: boolean;
  blocked_reason?: string;
}

export interface DeviceInfo {
  kind: string;
  name: string;
  memory_mb?: number;
  provider?: string;
  available?: boolean;
}

export interface Capabilities {
  engine_version: string;
  python_version: string;
  platform: string;
  methods: string[];
  devices: DeviceInfo[];
  runtime: RuntimeInfo;
}

export interface RuntimeInfo {
  available_providers: string[];
  cuda_usable: boolean;
  onnxruntime: string | null;
}

// ---- method params / results ----
/** Liveness check. The shell uses this to confirm the engine is responsive. */
export type SystemPingParams = Record<string, never>;

export interface SystemPingResult {
  pong: boolean;
  uptime_ms: number;
  engine_version: string;
}

/** Report platform, devices and the live method surface. */
export type SystemCapabilitiesParams = Record<string, never>;

export type SystemCapabilitiesResult = Capabilities;

/** Ask the engine to exit cleanly. The shell calls this before quit. */
export type SystemShutdownParams = Record<string, never>;

export interface SystemShutdownResult {
  ok: boolean;
}

/** Read video metadata via ffprobe. */
export interface MediaProbeParams {
  path: string;
}

export type MediaProbeResult = MediaInfo;

/** Clamp, order and default-fill annotation segments against video geometry. */
export interface AnnotationNormalizeParams {
  segments: Segment[];
  video: VideoMeta;
}

export interface AnnotationNormalizeResult {
  segments: Segment[];
}

/** Start a watermark-removal run. Returns a task id immediately. */
export interface ProcessStartParams {
  input_path: string;
  output_dir?: string;
  model_id?: string;
  segments: Segment[];
}

export interface ProcessStartResult {
  task_id: string;
}

/** Request cooperative cancellation of a running task. */
export interface ProcessCancelParams {
  task_id: string;
}

export interface ProcessCancelResult {
  ok: boolean;
}

/** Snapshot a processing task. */
export interface ProcessStatusParams {
  task_id: string;
}

export type ProcessStatusResult = TaskStatus;

/** Upscale a video 2x or 4x with Real-ESRGAN. Returns a task id; progress is streamed and the task is inspected through process.status / process.cancel. */
export interface UpscaleStartParams {
  input_path: string;
  output_dir?: string;
  scale?: number;
  model_id?: string;
}

export interface UpscaleStartResult {
  task_id: string;
}

/** List known models and their install state. */
export type ModelListParams = Record<string, never>;

export interface ModelListResult {
  models: ModelEntry[];
}

/** Download and verify a model. Returns a task id immediately. */
export interface ModelInstallParams {
  model_id: string;
  force?: boolean;
}

export interface ModelInstallResult {
  task_id: string;
}

/** Snapshot a model-install task. */
export interface ModelInstallStatusParams {
  task_id: string;
}

export type ModelInstallStatusResult = TaskStatus;

/** Check that required runtime assets (ffmpeg, models) are present. */
export type AssetsVerifyParams = Record<string, never>;

export interface AssetsVerifyResult {
  ok: boolean;
  missing: string[];
}

// ---- method surface ----
export type MethodName =
  | "system.ping"
  | "system.capabilities"
  | "system.shutdown"
  | "media.probe"
  | "annotation.normalize"
  | "process.start"
  | "process.cancel"
  | "process.status"
  | "upscale.start"
  | "model.list"
  | "model.install"
  | "model.installStatus"
  | "assets.verify";

export interface MethodParams {
  "system.ping": SystemPingParams;
  "system.capabilities": SystemCapabilitiesParams;
  "system.shutdown": SystemShutdownParams;
  "media.probe": MediaProbeParams;
  "annotation.normalize": AnnotationNormalizeParams;
  "process.start": ProcessStartParams;
  "process.cancel": ProcessCancelParams;
  "process.status": ProcessStatusParams;
  "upscale.start": UpscaleStartParams;
  "model.list": ModelListParams;
  "model.install": ModelInstallParams;
  "model.installStatus": ModelInstallStatusParams;
  "assets.verify": AssetsVerifyParams;
}

export interface MethodResults {
  "system.ping": SystemPingResult;
  "system.capabilities": SystemCapabilitiesResult;
  "system.shutdown": SystemShutdownResult;
  "media.probe": MediaProbeResult;
  "annotation.normalize": AnnotationNormalizeResult;
  "process.start": ProcessStartResult;
  "process.cancel": ProcessCancelResult;
  "process.status": ProcessStatusResult;
  "upscale.start": UpscaleStartResult;
  "model.list": ModelListResult;
  "model.install": ModelInstallResult;
  "model.installStatus": ModelInstallStatusResult;
  "assets.verify": AssetsVerifyResult;
}

export const IMPLEMENTED_METHODS = [
  "system.ping",
  "system.capabilities",
  "system.shutdown",
  "media.probe",
  "annotation.normalize",
  "process.start",
  "process.cancel",
  "process.status",
  "upscale.start",
  "model.list",
  "assets.verify",
] as const;

// ---- errors ----
export const ERROR_CODES = {
  "PARSE_ERROR": { jsonrpc: -32700, retryable: false, message_key: "err.parse" },
  "INVALID_REQUEST": { jsonrpc: -32600, retryable: false, message_key: "err.invalid_request" },
  "METHOD_NOT_FOUND": { jsonrpc: -32601, retryable: false, message_key: "err.method_not_found" },
  "INVALID_PARAMS": { jsonrpc: -32602, retryable: false, message_key: "err.invalid_params" },
  "INTERNAL_ERROR": { jsonrpc: -32603, retryable: true, message_key: "err.internal" },
  "NOT_IMPLEMENTED": { jsonrpc: -32001, retryable: false, message_key: "err.not_implemented" },
  "TASK_NOT_FOUND": { jsonrpc: -32002, retryable: false, message_key: "err.task_not_found" },
  "MODEL_MISSING": { jsonrpc: -32003, retryable: true, message_key: "err.model_missing" },
  "FFMPEG_MISSING": { jsonrpc: -32004, retryable: false, message_key: "err.ffmpeg_missing" },
  "CANCELLED": { jsonrpc: -32005, retryable: true, message_key: "err.cancelled" },
} as const;

export type ErrorCode = keyof typeof ERROR_CODES;
