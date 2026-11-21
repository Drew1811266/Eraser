/**
 * The engine half of the `window.wmr` compatibility layer.
 *
 * Deliberately free of Electron imports: the same code answers the ported
 * frontend inside the packaged app (via `wmr.ts`) and inside the browser
 * preview harness (`scripts/dev-preview.mjs`). Anything that needs a real
 * desktop — dialogs, shell, settings on disk — is injected through `WmrDeps`.
 */

export interface WmrSettings {
  language: 'zh' | 'en';
  theme: 'light' | 'dark';
  output: { path: string; model_id: string };
}

export interface WmrDeps {
  request: (method: string, params: Record<string, unknown>) => Promise<unknown>;
  readSettings: () => WmrSettings;
  writeSettings: (patch: Partial<WmrSettings>) => void;
  defaultOutputDir: () => string;
  openPath: (target: string) => Promise<void>;
  /** The UI passes a directory here; a file path still has to work. */
  isDirectory?: (target: string) => boolean;
  /** Delivers a progress frame to the UI (Electron: executeJavaScript). */
  pushProgress: (payload: Record<string, unknown>) => void;
  sleep?: (ms: number) => Promise<void>;
  activeTask: {
    get: () => string | null;
    set: (taskId: string | null) => void;
  };
}

const TERMINAL = ['success', 'failed', 'cancelled'];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function toEngineSegments(segments: unknown): unknown[] {
  if (!Array.isArray(segments)) return [];
  return segments.map((entry) => {
    const segment = entry as Record<string, unknown>;
    const rect = (segment.rect ?? {}) as Record<string, unknown>;
    return {
      start_frame: Math.round(Number(segment.start_frame ?? 0)),
      end_frame: Math.round(Number(segment.end_frame ?? 0)),
      rect: {
        x: Math.round(Number(rect.x ?? 0)),
        y: Math.round(Number(rect.y ?? 0)),
        width: Math.max(1, Math.round(Number(rect.width ?? 1))),
        height: Math.max(1, Math.round(Number(rect.height ?? 1))),
      },
      expand_px: Number(segment.expand_px ?? 5),
      feather_px: Number(segment.feather_px ?? 3),
      enabled: segment.enabled !== false,
    };
  });
}

export function progressFromStatus(status: Record<string, unknown>): Record<string, unknown> {
  const processed = Number(status.processed ?? 0);
  const total = Number(status.total ?? 0);
  const progress = Number(status.progress ?? 0);
  const detail: Record<string, unknown> = {
    progress: Math.min(1, Math.max(0, progress)),
    processed_frames: processed,
    total_frames: total,
    status: String(status.message ?? ''),
    phase: String(status.phase ?? ''),
  };
  const eta = status.eta_seconds;
  if (typeof eta === 'number') {
    detail.eta_seconds = eta;
    const seconds = Math.max(0, Math.round(eta));
    detail.estimated_time =
      `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  }
  if (total > 0 && processed > 0 && progress > 0) {
    detail.throughput_fps = Number((processed / (progress * total)).toFixed(2));
  }
  return detail;
}

/** Methods that go straight to the engine; everything else is host-specific. */
export const ENGINE_METHODS = new Set([
  'get_media_info', 'prepare_video_preview',
  'open_video_preview_session', 'read_video_preview_frame', 'close_video_preview_session',
  'load_annotations', 'save_annotations', 'delete_annotations',
  'get_settings', 'save_settings', 'get_device_info',
  'process_video', 'stop_processing', 'open_output_dir',
  'get_model_download_status', 'start_model_download', 'cancel_model_download',
  'get_upscale_model_download_status', 'start_upscale_model_download', 'cancel_upscale_model_download',
  'get_upscale_capabilities', 'start_upscale', 'get_upscale_task_status', 'cancel_upscale_task',
]);

export async function wmrEngineCall(
  deps: WmrDeps,
  method: string,
  payload: Record<string, unknown> = {},
): Promise<unknown> {
  const wait = deps.sleep ?? sleep;

  switch (method) {
    case 'get_media_info': {
      const info = (await deps.request('media.probe', { path: payload.path })) as Record<string, unknown>;
      return {
        success: true,
        type: 'video',
        fps: info.fps,
        frame_count: info.frame_count,
        duration: info.duration_seconds,
        width: info.width,
        height: info.height,
      };
    }

    case 'prepare_video_preview':
      return { success: true, path: payload.path, transcoded: false };

    case 'open_video_preview_session': {
      const session = (await deps.request('preview.open', {
        path: payload.path,
        max_width: payload.max_width ?? 1280,
      })) as Record<string, unknown>;
      return {
        success: true,
        session_id: session.session_id,
        preview_fps: session.fps,
        total_preview_frames: session.frame_count,
        width: session.preview_width ?? session.width,
        height: session.preview_height ?? session.height,
      };
    }

    case 'read_video_preview_frame': {
      const frame = (await deps.request('preview.frame', {
        session_id: payload.session_id,
        frame_index: payload.frame_index,
      })) as Record<string, unknown>;
      return { success: true, frame_index: frame.frame_index, frame_url: frame.data_url };
    }

    case 'close_video_preview_session':
      await deps.request('preview.close', { session_id: payload.session_id });
      return { success: true };

    case 'load_annotations': {
      const loaded = (await deps.request('annotation.load', {
        video_path: payload.video_path,
      })) as Record<string, unknown>;
      return {
        success: true,
        exists: loaded.exists,
        sidecar_path: loaded.sidecar_path,
        segments: loaded.segments ?? [],
      };
    }

    case 'save_annotations': {
      const saved = (await deps.request('annotation.save', {
        video_path: payload.video_path,
        segments: toEngineSegments(payload.segments),
      })) as Record<string, unknown>;
      return { success: true, sidecar_path: saved.sidecar_path, segments: saved.segments };
    }

    case 'delete_annotations': {
      const removed = (await deps.request('annotation.delete', {
        video_path: payload.video_path,
      })) as Record<string, unknown>;
      return { success: removed.ok !== false };
    }

    case 'get_settings': {
      const settings = deps.readSettings();
      return {
        language: settings.language,
        theme: settings.theme,
        output: {
          path: settings.output.path || deps.defaultOutputDir(),
          model_id: settings.output.model_id,
        },
      };
    }

    case 'save_settings':
      deps.writeSettings(payload as unknown as Partial<WmrSettings>);
      return { success: true };

    case 'get_device_info': {
      const capabilities = (await deps.request('system.capabilities', {})) as Record<string, unknown>;
      const devices = (capabilities.devices as Array<Record<string, unknown>>) ?? [];
      const gpu = devices.find((entry) => entry.kind === 'cuda');
      const runtime = (capabilities.runtime as Record<string, unknown>) ?? {};
      const name = gpu ? String(gpu.name) : String(devices[0]?.name ?? 'CPU');
      const memory = gpu?.memory_mb ? `${(Number(gpu.memory_mb) / 1024).toFixed(1)}GB` : '--';
      return {
        device: `${name}${runtime.cuda_usable ? '' : ' (CPU)'}`,
        memory,
        supports_fp16: Boolean(runtime.cuda_usable),
      };
    }

    case 'process_video': {
      const segments = toEngineSegments(payload.annotation_segments);
      if (segments.length === 0) {
        return { success: false, error: '没有可用的标注区域' };
      }
      const settings = (payload.settings as Record<string, unknown>) ?? {};
      const modelId = String(settings.model_id ?? deps.readSettings().output.model_id ?? 'lama_roi');
      const requested = String(payload.output_path ?? deps.defaultOutputDir());
      const outputDir = deps.isDirectory?.(requested) ? requested : dirnameOf(requested);

      const started = (await deps.request('process.start', {
        input_path: payload.input_path,
        output_dir: outputDir,
        model_id: modelId,
        segments,
      })) as { task_id: string };
      deps.activeTask.set(started.task_id);

      let status: Record<string, unknown> = {};
      for (;;) {
        status = (await deps.request('process.status', { task_id: started.task_id })) as Record<string, unknown>;
        deps.pushProgress(progressFromStatus(status));
        if (TERMINAL.includes(String(status.state))) break;
        await wait(200);
      }
      deps.activeTask.set(null);

      const result = (status.result as Record<string, unknown>) ?? {};
      if (status.state === 'success') {
        return {
          success: true,
          output_path: result.output_path,
          requested_model_id: result.requested_model_id ?? modelId,
          effective_model_id: result.effective_model_id ?? modelId,
          model_warning: result.model_warning ?? '',
        };
      }
      return { success: false, error: String(status.error ?? 'processing failed') };
    }

    case 'stop_processing': {
      const task = deps.activeTask.get();
      if (task) await deps.request('process.cancel', { task_id: task });
      return { success: true };
    }

    case 'open_output_dir': {
      const target = String(payload.path ?? deps.defaultOutputDir());
      await deps.openPath(dirnameOf(target));
      return { success: true };
    }

    case 'get_model_download_status': {
      const status = (await deps.request('model.download.status', {})) as Record<string, unknown>;
      return { success: true, task: status };
    }

    case 'start_model_download':
      await deps.request('model.download.start', { model_id: payload.model_id });
      return { success: true };

    case 'cancel_model_download':
      await deps.request('model.download.cancel', {});
      return { success: true };

    case 'get_upscale_model_download_status': {
      // Same installer as the import screen: a SeedVR2 model brings its runtime
      // with it, and the UI polls one status for both.
      const status = (await deps.request('model.download.status', {})) as Record<string, unknown>;
      return { success: true, task: status };
    }
    case 'start_upscale_model_download':
      await deps.request('model.download.start', { model_id: payload.model_id });
      return { success: true };
    case 'cancel_upscale_model_download':
      await deps.request('model.download.cancel', {});
      return { success: true };

    case 'get_upscale_capabilities': {
      const capabilities = (await deps.request('upscale.capabilities', {})) as Record<string, unknown>;
      return { success: true, ...capabilities };
    }

    case 'start_upscale': {
      const config = (payload.config as Record<string, unknown>) ?? payload;
      const started = (await deps.request('upscale.start', {
        input_path: payload.input_path,
        output_dir: payload.output_dir ? String(payload.output_dir) : undefined,
        mode: config.mode ?? payload.mode,
        engine: config.engine ?? payload.engine,
        model_id: config.modelId ?? config.model_id ?? payload.model_id,
        target_preset: config.targetPreset ?? config.target_preset ?? payload.target_preset,
        same_res_strength: config.sameResStrength ?? config.same_res_strength ?? payload.same_res_strength,
        denoise_strength: config.denoiseStrength ?? config.denoise_strength ?? payload.denoise_strength,
        keep_audio: config.keepAudio ?? config.keep_audio ?? payload.keep_audio,
      })) as { task_id: string };
      deps.activeTask.set(started.task_id);
      return { success: true };
    }

    case 'get_upscale_task_status': {
      const task = deps.activeTask.get();
      if (!task) return { success: true, task: { state: 'idle', progress: 0 } };
      const status = (await deps.request('process.status', { task_id: task })) as Record<string, unknown>;
      const result = (status.result as Record<string, unknown>) ?? {};
      if (TERMINAL.includes(String(status.state))) deps.activeTask.set(null);
      return {
        success: true,
        task: {
          state: status.state,
          progress: status.progress,
          phase: status.phase,
          message: status.message,
          eta_seconds: status.eta_seconds ?? undefined,
          output_path: result.output_path,
          error: status.error,
          mode: result.mode,
          effective_engine: result.engine,
          model_id: result.model_id ?? config_model(deps),
          scene_split_mode: result.scene_split_mode ?? result.scene_split,
          segment_index: result.segment_index ?? status.segment_index,
          segment_total: result.segment_total ?? status.segment_total,
        },
      };
    }

    case 'cancel_upscale_task': {
      const task = deps.activeTask.get();
      if (task) {
        await deps.request('process.cancel', { task_id: task });
        deps.activeTask.set(null);
      }
      return { success: true };
    }

    default:
      return { success: false, error: `未实现的方法: ${method}` };
  }
}

/** The model the user picked; the engine reports what it actually ran. */
function config_model(deps: WmrDeps): string {
  return deps.readSettings().output.model_id;
}

function dirnameOf(target: string): string {
  const index = Math.max(target.lastIndexOf('/'), target.lastIndexOf('\\'));
  return index > 0 ? target.slice(0, index) : target;
}
