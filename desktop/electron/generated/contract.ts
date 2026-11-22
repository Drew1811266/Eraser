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
  keyframes?: RectKeyframe[];
}

export interface FrameInterval {
  start_frame: number;
  end_frame: number;
}

export interface RectKeyframe {
  frame: number;
  rect: Rect;
}

export interface WatermarkTrack {
  id: string;
  intervals: FrameInterval[];
  keyframes: RectKeyframe[];
  motion?: string;
  confidence?: number;
  source?: string;
  notes?: string[];
}

export interface DetectorEntry {
  model_id: string;
  display_name: string;
  installed: boolean;
  available: boolean;
  blocked_reason?: string;
}

export interface AnalysisVideo {
  width: number;
  height: number;
  fps: number;
  frame_count: number;
  duration_seconds: number;
}

export interface SuggestedStrategy {
  engine_id: string;
  split_by_shot: boolean;
  warnings: string[];
}

export interface Analysis {
  video: AnalysisVideo;
  shots: FrameInterval[];
  watermarks: WatermarkTrack[];
  suggested: SuggestedStrategy;
  model_id: string;
  elapsed_ms: number;
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
  result?: Record<string, unknown>;
  error?: string;
  segment_index?: number;
  segment_total?: number;
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

/** Open a preview session for a video so frames can be read one at a time. */
export interface PreviewOpenParams {
  path: string;
  max_width?: number;
}

export interface PreviewOpenResult {
  session_id: string;
  width: number;
  height: number;
  fps: number;
  frame_count: number;
  preview_width?: number;
  preview_height?: number;
}

/** Return one preview frame as a JPEG data URL, scaled for the canvas. */
export interface PreviewFrameParams {
  session_id: string;
  frame_index: number;
}

export interface PreviewFrameResult {
  session_id: string;
  frame_index: number;
  data_url: string;
  width?: number;
  height?: number;
}

/** Close a preview session and drop its temporary files. */
export interface PreviewCloseParams {
  session_id: string;
}

export interface PreviewCloseResult {
  ok: boolean;
}

/** Clamp, order and default-fill annotation segments against video geometry. */
export interface AnnotationNormalizeParams {
  segments: Segment[];
  video: VideoMeta;
}

export interface AnnotationNormalizeResult {
  segments: Segment[];
}

/** Remove the annotation sidecar for a video. */
export interface AnnotationDeleteParams {
  video_path: string;
}

export interface AnnotationDeleteResult {
  ok: boolean;
  deleted?: boolean;
}

/** Persist annotations for a video, returning the sidecar path. */
export interface AnnotationSaveParams {
  video_path: string;
  segments: Segment[];
}

export interface AnnotationSaveResult {
  sidecar_path: string;
  segments?: Segment[];
}

/** Load the annotation sidecar stored next to a video. */
export interface AnnotationLoadParams {
  video_path: string;
}

export interface AnnotationLoadResult {
  exists: boolean;
  sidecar_path?: string;
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

/** What upscaling can actually do here: engines, models, modes, and why an engine is unavailable. */
export type UpscaleCapabilitiesParams = Record<string, never>;

export interface UpscaleCapabilitiesResult {
  engines: Record<string, unknown>[];
  models: Record<string, unknown>[];
  modes: string[];
  target_presets: string[];
  same_res_strengths: string[];
  defaults: Record<string, unknown>;
  seedvr?: Record<string, unknown>;
}

/** Upscale a video. Two modes: raise the resolution to a target preset, or enhance at the source resolution. Optional scene splitting, denoise and audio retention. Returns a task id; progress is streamed and the task is inspected through process.status / process.cancel. */
export interface UpscaleStartParams {
  input_path: string;
  output_dir?: string;
  mode?: string;
  engine?: string;
  model_id?: string;
  target_preset?: string;
  same_res_strength?: string;
  denoise_strength?: number;
  keep_audio?: boolean;
  scene_split?: string;
  scale?: number;
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

/** Cancel an in-flight model download. */
export type ModelDownloadCancelParams = Record<string, never>;

export interface ModelDownloadCancelResult {
  ok: boolean;
}

/** Download the weights for a model that is not installed. */
export interface ModelDownloadStartParams {
  model_id: string;
}

export interface ModelDownloadStartResult {
  ok: boolean;
}

/** Report the state of the model downloader. */
export type ModelDownloadStatusParams = Record<string, never>;

export interface ModelDownloadStatusResult {
  state: string;
  model_id?: string;
  progress: number;
  received_bytes?: number;
  total_bytes?: number;
  error?: string;
  installed: boolean;
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

/** Report which watermark detectors can run on this machine. */
export type AnalyzeCapabilitiesParams = Record<string, never>;

export interface AnalyzeCapabilitiesResult {
  detectors: DetectorEntry[];
  available: boolean;
}

/** Analyse a video: shots, watermark tracks, intervals and motion. */
export interface AnalyzeStartParams {
  input_path: string;
  sample_step?: number;
  max_tracks?: number;
  min_confidence?: number;
}

export interface AnalyzeStartResult {
  task_id: string;
}

/** Turn watermark tracks into the segment list the pipeline consumes. */
export interface AnnotationCompileParams {
  watermarks: WatermarkTrack[];
  shots: FrameInterval[];
  video: { width: number; height: number; frame_count: number; };
}

export interface AnnotationCompileResult {
  segments: Segment[];
}

// ---- method surface ----
export type MethodName =
  | "system.ping"
  | "system.capabilities"
  | "system.shutdown"
  | "media.probe"
  | "preview.open"
  | "preview.frame"
  | "preview.close"
  | "annotation.normalize"
  | "annotation.delete"
  | "annotation.save"
  | "annotation.load"
  | "process.start"
  | "process.cancel"
  | "process.status"
  | "upscale.capabilities"
  | "upscale.start"
  | "model.list"
  | "model.install"
  | "model.download.cancel"
  | "model.download.start"
  | "model.download.status"
  | "model.installStatus"
  | "assets.verify"
  | "analyze.capabilities"
  | "analyze.start"
  | "annotation.compile";

export interface MethodParams {
  "system.ping": SystemPingParams;
  "system.capabilities": SystemCapabilitiesParams;
  "system.shutdown": SystemShutdownParams;
  "media.probe": MediaProbeParams;
  "preview.open": PreviewOpenParams;
  "preview.frame": PreviewFrameParams;
  "preview.close": PreviewCloseParams;
  "annotation.normalize": AnnotationNormalizeParams;
  "annotation.delete": AnnotationDeleteParams;
  "annotation.save": AnnotationSaveParams;
  "annotation.load": AnnotationLoadParams;
  "process.start": ProcessStartParams;
  "process.cancel": ProcessCancelParams;
  "process.status": ProcessStatusParams;
  "upscale.capabilities": UpscaleCapabilitiesParams;
  "upscale.start": UpscaleStartParams;
  "model.list": ModelListParams;
  "model.install": ModelInstallParams;
  "model.download.cancel": ModelDownloadCancelParams;
  "model.download.start": ModelDownloadStartParams;
  "model.download.status": ModelDownloadStatusParams;
  "model.installStatus": ModelInstallStatusParams;
  "assets.verify": AssetsVerifyParams;
  "analyze.capabilities": AnalyzeCapabilitiesParams;
  "analyze.start": AnalyzeStartParams;
  "annotation.compile": AnnotationCompileParams;
}

export interface MethodResults {
  "system.ping": SystemPingResult;
  "system.capabilities": SystemCapabilitiesResult;
  "system.shutdown": SystemShutdownResult;
  "media.probe": MediaProbeResult;
  "preview.open": PreviewOpenResult;
  "preview.frame": PreviewFrameResult;
  "preview.close": PreviewCloseResult;
  "annotation.normalize": AnnotationNormalizeResult;
  "annotation.delete": AnnotationDeleteResult;
  "annotation.save": AnnotationSaveResult;
  "annotation.load": AnnotationLoadResult;
  "process.start": ProcessStartResult;
  "process.cancel": ProcessCancelResult;
  "process.status": ProcessStatusResult;
  "upscale.capabilities": UpscaleCapabilitiesResult;
  "upscale.start": UpscaleStartResult;
  "model.list": ModelListResult;
  "model.install": ModelInstallResult;
  "model.download.cancel": ModelDownloadCancelResult;
  "model.download.start": ModelDownloadStartResult;
  "model.download.status": ModelDownloadStatusResult;
  "model.installStatus": ModelInstallStatusResult;
  "assets.verify": AssetsVerifyResult;
  "analyze.capabilities": AnalyzeCapabilitiesResult;
  "analyze.start": AnalyzeStartResult;
  "annotation.compile": AnnotationCompileResult;
}

export const IMPLEMENTED_METHODS = [
  "system.ping",
  "system.capabilities",
  "system.shutdown",
  "media.probe",
  "preview.open",
  "preview.frame",
  "preview.close",
  "annotation.normalize",
  "annotation.delete",
  "annotation.save",
  "annotation.load",
  "process.start",
  "process.cancel",
  "process.status",
  "upscale.capabilities",
  "upscale.start",
  "model.list",
  "model.install",
  "model.download.cancel",
  "model.download.start",
  "model.download.status",
  "model.installStatus",
  "assets.verify",
  "analyze.capabilities",
  "analyze.start",
  "annotation.compile",
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
  "DETECTOR_MISSING": { jsonrpc: -32006, retryable: true, message_key: "err.detector_missing" },
} as const;

export type ErrorCode = keyof typeof ERROR_CODES;
