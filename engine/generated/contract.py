"""AUTO-GENERATED FROM contract/methods.json + contract/errors.json. DO NOT EDIT."""

from __future__ import annotations

import json as _json

CONTRACT_VERSION = "1.0.0"

_CONTRACT_JSON = r"""
{
  "contractVersion": "1.0.0",
  "types": {
    "Rect": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "x": {
          "type": "integer"
        },
        "y": {
          "type": "integer"
        },
        "width": {
          "type": "integer"
        },
        "height": {
          "type": "integer"
        }
      },
      "required": [
        "x",
        "y",
        "width",
        "height"
      ]
    },
    "VideoMeta": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "width": {
          "type": "integer"
        },
        "height": {
          "type": "integer"
        },
        "frame_count": {
          "type": "integer"
        }
      },
      "required": [
        "width",
        "height",
        "frame_count"
      ]
    },
    "Segment": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "id": {
          "type": "string"
        },
        "start_frame": {
          "type": "integer"
        },
        "end_frame": {
          "type": "integer"
        },
        "rect": {
          "$ref": "#/types/Rect"
        },
        "expand_px": {
          "type": "integer"
        },
        "feather_px": {
          "type": "integer"
        },
        "enabled": {
          "type": "boolean"
        },
        "created_at": {
          "type": "string"
        },
        "updated_at": {
          "type": "string"
        },
        "keyframes": {
          "type": "array",
          "items": {
            "$ref": "#/types/RectKeyframe"
          }
        }
      },
      "required": [
        "start_frame",
        "end_frame",
        "rect"
      ]
    },
    "FrameInterval": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "start_frame": {
          "type": "integer"
        },
        "end_frame": {
          "type": "integer"
        }
      },
      "required": [
        "start_frame",
        "end_frame"
      ]
    },
    "RectKeyframe": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "frame": {
          "type": "integer"
        },
        "rect": {
          "$ref": "#/types/Rect"
        }
      },
      "required": [
        "frame",
        "rect"
      ]
    },
    "WatermarkTrack": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "id": {
          "type": "string"
        },
        "intervals": {
          "type": "array",
          "items": {
            "$ref": "#/types/FrameInterval"
          }
        },
        "keyframes": {
          "type": "array",
          "items": {
            "$ref": "#/types/RectKeyframe"
          }
        },
        "motion": {
          "type": "string"
        },
        "confidence": {
          "type": "number"
        },
        "source": {
          "type": "string"
        },
        "notes": {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      },
      "required": [
        "id",
        "intervals",
        "keyframes"
      ]
    },
    "DetectorEntry": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "model_id": {
          "type": "string"
        },
        "display_name": {
          "type": "string"
        },
        "installed": {
          "type": "boolean"
        },
        "available": {
          "type": "boolean"
        },
        "blocked_reason": {
          "type": "string"
        }
      },
      "required": [
        "model_id",
        "display_name",
        "installed",
        "available"
      ]
    },
    "AnalysisVideo": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "width": {
          "type": "integer"
        },
        "height": {
          "type": "integer"
        },
        "fps": {
          "type": "number"
        },
        "frame_count": {
          "type": "integer"
        },
        "duration_seconds": {
          "type": "number"
        }
      },
      "required": [
        "width",
        "height",
        "fps",
        "frame_count",
        "duration_seconds"
      ]
    },
    "SuggestedStrategy": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "engine_id": {
          "type": "string"
        },
        "split_by_shot": {
          "type": "boolean"
        },
        "warnings": {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      },
      "required": [
        "engine_id",
        "split_by_shot",
        "warnings"
      ]
    },
    "Analysis": {
      "type": "object",
      "description": "Payload of a successful analyze task, delivered through TaskStatus.result.",
      "additionalProperties": false,
      "properties": {
        "video": {
          "$ref": "#/types/AnalysisVideo"
        },
        "shots": {
          "type": "array",
          "items": {
            "$ref": "#/types/FrameInterval"
          }
        },
        "watermarks": {
          "type": "array",
          "items": {
            "$ref": "#/types/WatermarkTrack"
          }
        },
        "suggested": {
          "$ref": "#/types/SuggestedStrategy"
        },
        "model_id": {
          "type": "string"
        },
        "elapsed_ms": {
          "type": "integer"
        }
      },
      "required": [
        "video",
        "shots",
        "watermarks",
        "suggested",
        "model_id",
        "elapsed_ms"
      ]
    },
    "MediaInfo": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string"
        },
        "width": {
          "type": "integer"
        },
        "height": {
          "type": "integer"
        },
        "fps": {
          "type": "number"
        },
        "frame_count": {
          "type": "integer"
        },
        "duration_seconds": {
          "type": "number"
        },
        "has_audio": {
          "type": "boolean"
        },
        "video_codec": {
          "type": "string"
        },
        "container": {
          "type": "string"
        }
      },
      "required": [
        "path",
        "width",
        "height",
        "fps",
        "frame_count",
        "has_audio"
      ]
    },
    "TaskStatus": {
      "type": "object",
      "properties": {
        "task_id": {
          "type": "string"
        },
        "state": {
          "type": "string"
        },
        "phase": {
          "type": "string"
        },
        "progress": {
          "type": "number"
        },
        "processed": {
          "type": "integer"
        },
        "total": {
          "type": "integer"
        },
        "eta_seconds": {
          "type": [
            "number",
            "null"
          ]
        },
        "message": {
          "type": "string"
        },
        "result": {
          "type": "object",
          "additionalProperties": true,
          "description": "Result payload of a finished task; shape depends on the method."
        },
        "error": {
          "type": "string"
        },
        "segment_index": {
          "type": "integer"
        },
        "segment_total": {
          "type": "integer"
        }
      },
      "required": [
        "task_id",
        "state",
        "phase",
        "progress"
      ]
    },
    "ModelEntry": {
      "type": "object",
      "properties": {
        "model_id": {
          "type": "string"
        },
        "display_name": {
          "type": "string"
        },
        "engine": {
          "type": "string"
        },
        "installed": {
          "type": "boolean"
        },
        "implemented": {
          "type": "boolean"
        },
        "blocked_reason": {
          "type": "string"
        }
      },
      "required": [
        "model_id",
        "display_name",
        "engine",
        "installed",
        "implemented"
      ]
    },
    "DeviceInfo": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string"
        },
        "name": {
          "type": "string"
        },
        "memory_mb": {
          "type": "number"
        },
        "provider": {
          "type": "string"
        },
        "available": {
          "type": "boolean"
        }
      },
      "required": [
        "kind",
        "name"
      ]
    },
    "Capabilities": {
      "type": "object",
      "properties": {
        "engine_version": {
          "type": "string"
        },
        "python_version": {
          "type": "string"
        },
        "platform": {
          "type": "string"
        },
        "methods": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "devices": {
          "type": "array",
          "items": {
            "$ref": "#/types/DeviceInfo"
          }
        },
        "runtime": {
          "$ref": "#/types/RuntimeInfo"
        }
      },
      "required": [
        "engine_version",
        "python_version",
        "platform",
        "methods",
        "devices",
        "runtime"
      ]
    },
    "RuntimeInfo": {
      "type": "object",
      "properties": {
        "available_providers": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "cuda_usable": {
          "type": "boolean"
        },
        "onnxruntime": {
          "type": [
            "string",
            "null"
          ]
        }
      },
      "required": [
        "available_providers",
        "cuda_usable",
        "onnxruntime"
      ]
    }
  },
  "methods": [
    {
      "name": "system.ping",
      "summary": "Liveness check. The shell uses this to confirm the engine is responsive.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {}
      },
      "result": {
        "type": "object",
        "properties": {
          "pong": {
            "type": "boolean"
          },
          "uptime_ms": {
            "type": "number"
          },
          "engine_version": {
            "type": "string"
          }
        },
        "required": [
          "pong",
          "uptime_ms",
          "engine_version"
        ]
      }
    },
    {
      "name": "system.capabilities",
      "summary": "Report platform, devices and the live method surface.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {}
      },
      "result": {
        "$ref": "#/types/Capabilities"
      }
    },
    {
      "name": "system.shutdown",
      "summary": "Ask the engine to exit cleanly. The shell calls this before quit.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {}
      },
      "result": {
        "type": "object",
        "properties": {
          "ok": {
            "type": "boolean"
          }
        },
        "required": [
          "ok"
        ]
      }
    },
    {
      "name": "media.probe",
      "summary": "Read video metadata via ffprobe.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "path": {
            "type": "string"
          }
        },
        "required": [
          "path"
        ]
      },
      "result": {
        "$ref": "#/types/MediaInfo"
      }
    },
    {
      "name": "preview.open",
      "summary": "Open a preview session for a video so frames can be read one at a time.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "path": {
            "type": "string"
          },
          "max_width": {
            "type": "integer"
          }
        },
        "required": [
          "path"
        ]
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "session_id": {
            "type": "string"
          },
          "width": {
            "type": "integer"
          },
          "height": {
            "type": "integer"
          },
          "fps": {
            "type": "number"
          },
          "frame_count": {
            "type": "integer"
          },
          "preview_width": {
            "type": "integer"
          },
          "preview_height": {
            "type": "integer"
          }
        },
        "required": [
          "session_id",
          "width",
          "height",
          "fps",
          "frame_count"
        ]
      }
    },
    {
      "name": "preview.frame",
      "summary": "Return one preview frame as a JPEG data URL, scaled for the canvas.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "session_id": {
            "type": "string"
          },
          "frame_index": {
            "type": "integer"
          }
        },
        "required": [
          "session_id",
          "frame_index"
        ]
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "session_id": {
            "type": "string"
          },
          "frame_index": {
            "type": "integer"
          },
          "data_url": {
            "type": "string"
          },
          "width": {
            "type": "integer"
          },
          "height": {
            "type": "integer"
          }
        },
        "required": [
          "session_id",
          "frame_index",
          "data_url"
        ]
      }
    },
    {
      "name": "preview.close",
      "summary": "Close a preview session and drop its temporary files.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "session_id": {
            "type": "string"
          }
        },
        "required": [
          "session_id"
        ]
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "ok": {
            "type": "boolean"
          }
        },
        "required": [
          "ok"
        ]
      }
    },
    {
      "name": "annotation.normalize",
      "summary": "Clamp, order and default-fill annotation segments against video geometry.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "segments": {
            "type": "array",
            "items": {
              "$ref": "#/types/Segment"
            }
          },
          "video": {
            "$ref": "#/types/VideoMeta"
          }
        },
        "required": [
          "segments",
          "video"
        ]
      },
      "result": {
        "type": "object",
        "properties": {
          "segments": {
            "type": "array",
            "items": {
              "$ref": "#/types/Segment"
            }
          }
        },
        "required": [
          "segments"
        ]
      }
    },
    {
      "name": "annotation.delete",
      "summary": "Remove the annotation sidecar for a video.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "video_path": {
            "type": "string"
          }
        },
        "required": [
          "video_path"
        ]
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "ok": {
            "type": "boolean"
          },
          "deleted": {
            "type": "boolean"
          }
        },
        "required": [
          "ok"
        ]
      }
    },
    {
      "name": "annotation.save",
      "summary": "Persist annotations for a video, returning the sidecar path.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "video_path": {
            "type": "string"
          },
          "segments": {
            "type": "array",
            "items": {
              "$ref": "#/types/Segment"
            }
          }
        },
        "required": [
          "video_path",
          "segments"
        ]
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "sidecar_path": {
            "type": "string"
          },
          "segments": {
            "type": "array",
            "items": {
              "$ref": "#/types/Segment"
            }
          }
        },
        "required": [
          "sidecar_path"
        ]
      }
    },
    {
      "name": "annotation.load",
      "summary": "Load the annotation sidecar stored next to a video.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "video_path": {
            "type": "string"
          }
        },
        "required": [
          "video_path"
        ]
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "exists": {
            "type": "boolean"
          },
          "sidecar_path": {
            "type": "string"
          },
          "segments": {
            "type": "array",
            "items": {
              "$ref": "#/types/Segment"
            }
          }
        },
        "required": [
          "exists",
          "segments"
        ]
      }
    },
    {
      "name": "process.start",
      "summary": "Start a watermark-removal run. Returns a task id immediately.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "input_path": {
            "type": "string"
          },
          "output_dir": {
            "type": "string"
          },
          "model_id": {
            "type": "string"
          },
          "segments": {
            "type": "array",
            "items": {
              "$ref": "#/types/Segment"
            }
          }
        },
        "required": [
          "input_path",
          "segments"
        ]
      },
      "result": {
        "type": "object",
        "properties": {
          "task_id": {
            "type": "string"
          }
        },
        "required": [
          "task_id"
        ]
      }
    },
    {
      "name": "process.cancel",
      "summary": "Request cooperative cancellation of a running task.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "task_id": {
            "type": "string"
          }
        },
        "required": [
          "task_id"
        ]
      },
      "result": {
        "type": "object",
        "properties": {
          "ok": {
            "type": "boolean"
          }
        },
        "required": [
          "ok"
        ]
      }
    },
    {
      "name": "process.status",
      "summary": "Snapshot a processing task.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "task_id": {
            "type": "string"
          }
        },
        "required": [
          "task_id"
        ]
      },
      "result": {
        "$ref": "#/types/TaskStatus"
      }
    },
    {
      "name": "upscale.capabilities",
      "summary": "What upscaling can actually do here: engines, models, modes, and why an engine is unavailable.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {}
      },
      "result": {
        "type": "object",
        "properties": {
          "engines": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": true
            }
          },
          "models": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": true
            }
          },
          "modes": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "target_presets": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "same_res_strengths": {
            "type": "array",
            "items": {
              "type": "string"
            }
          },
          "defaults": {
            "type": "object",
            "additionalProperties": true
          },
          "seedvr": {
            "type": "object",
            "additionalProperties": true
          }
        },
        "required": [
          "engines",
          "models",
          "modes",
          "target_presets",
          "same_res_strengths",
          "defaults"
        ]
      }
    },
    {
      "name": "upscale.start",
      "summary": "Upscale a video. Two modes: raise the resolution to a target preset, or enhance at the source resolution. Optional scene splitting, denoise and audio retention. Returns a task id; progress is streamed and the task is inspected through process.status / process.cancel.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "input_path": {
            "type": "string"
          },
          "output_dir": {
            "type": "string"
          },
          "mode": {
            "type": "string",
            "enum": [
              "upscale_resolution",
              "enhance_same_resolution"
            ]
          },
          "engine": {
            "type": "string",
            "enum": [
              "realesrgan",
              "seedvr2"
            ]
          },
          "model_id": {
            "type": "string"
          },
          "target_preset": {
            "type": "string",
            "enum": [
              "1080p",
              "1440p",
              "4k"
            ]
          },
          "same_res_strength": {
            "type": "string",
            "enum": [
              "x2_then_downscale"
            ]
          },
          "denoise_strength": {
            "type": "number"
          },
          "keep_audio": {
            "type": "boolean"
          },
          "scene_split": {
            "type": "string",
            "enum": [
              "off",
              "rule",
              "hybrid"
            ]
          },
          "scale": {
            "type": "integer",
            "enum": [
              2,
              4
            ]
          }
        },
        "required": [
          "input_path"
        ]
      },
      "result": {
        "type": "object",
        "properties": {
          "task_id": {
            "type": "string"
          }
        },
        "required": [
          "task_id"
        ]
      }
    },
    {
      "name": "model.list",
      "summary": "List known models and their install state.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {}
      },
      "result": {
        "type": "object",
        "properties": {
          "models": {
            "type": "array",
            "items": {
              "$ref": "#/types/ModelEntry"
            }
          }
        },
        "required": [
          "models"
        ]
      }
    },
    {
      "name": "model.install",
      "summary": "Download and verify a model. Returns a task id immediately.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "model_id": {
            "type": "string"
          },
          "force": {
            "type": "boolean"
          }
        },
        "required": [
          "model_id"
        ]
      },
      "result": {
        "type": "object",
        "properties": {
          "task_id": {
            "type": "string"
          }
        },
        "required": [
          "task_id"
        ]
      }
    },
    {
      "name": "model.download.cancel",
      "summary": "Cancel an in-flight model download.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {},
        "required": []
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "ok": {
            "type": "boolean"
          }
        },
        "required": [
          "ok"
        ]
      }
    },
    {
      "name": "model.download.start",
      "summary": "Download the weights for a model that is not installed.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "model_id": {
            "type": "string"
          }
        },
        "required": [
          "model_id"
        ]
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "ok": {
            "type": "boolean"
          }
        },
        "required": [
          "ok"
        ]
      }
    },
    {
      "name": "model.download.status",
      "summary": "Report the state of the model downloader.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {},
        "required": []
      },
      "result": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "state": {
            "type": "string"
          },
          "model_id": {
            "type": "string"
          },
          "progress": {
            "type": "number"
          },
          "received_bytes": {
            "type": "integer"
          },
          "total_bytes": {
            "type": "integer"
          },
          "error": {
            "type": "string"
          },
          "installed": {
            "type": "boolean"
          }
        },
        "required": [
          "state",
          "progress",
          "installed"
        ]
      }
    },
    {
      "name": "model.installStatus",
      "summary": "Snapshot a model-install task.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "task_id": {
            "type": "string"
          }
        },
        "required": [
          "task_id"
        ]
      },
      "result": {
        "$ref": "#/types/TaskStatus"
      }
    },
    {
      "name": "assets.verify",
      "summary": "Check that required runtime assets (ffmpeg, models) are present.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {}
      },
      "result": {
        "type": "object",
        "properties": {
          "ok": {
            "type": "boolean"
          },
          "missing": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "ok",
          "missing"
        ]
      }
    },
    {
      "name": "analyze.capabilities",
      "summary": "Report which watermark detectors can run on this machine.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {}
      },
      "result": {
        "type": "object",
        "properties": {
          "detectors": {
            "type": "array",
            "items": {
              "$ref": "#/types/DetectorEntry"
            }
          },
          "available": {
            "type": "boolean"
          }
        },
        "required": [
          "detectors",
          "available"
        ]
      }
    },
    {
      "name": "analyze.start",
      "summary": "Analyse a video: shots, watermark tracks, intervals and motion.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "input_path": {
            "type": "string"
          },
          "sample_step": {
            "type": "integer"
          },
          "max_tracks": {
            "type": "integer"
          },
          "min_confidence": {
            "type": "number"
          }
        },
        "required": [
          "input_path"
        ]
      },
      "result": {
        "type": "object",
        "properties": {
          "task_id": {
            "type": "string"
          }
        },
        "required": [
          "task_id"
        ]
      }
    },
    {
      "name": "annotation.compile",
      "summary": "Turn watermark tracks into the segment list the pipeline consumes.",
      "implemented": true,
      "params": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "watermarks": {
            "type": "array",
            "items": {
              "$ref": "#/types/WatermarkTrack"
            }
          },
          "shots": {
            "type": "array",
            "items": {
              "$ref": "#/types/FrameInterval"
            }
          },
          "video": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "width": {
                "type": "integer"
              },
              "height": {
                "type": "integer"
              },
              "frame_count": {
                "type": "integer"
              }
            },
            "required": [
              "width",
              "height",
              "frame_count"
            ]
          }
        },
        "required": [
          "watermarks",
          "shots",
          "video"
        ]
      },
      "result": {
        "type": "object",
        "properties": {
          "segments": {
            "type": "array",
            "items": {
              "$ref": "#/types/Segment"
            }
          }
        },
        "required": [
          "segments"
        ]
      }
    }
  ],
  "error_codes": [
    {
      "code": "PARSE_ERROR",
      "jsonrpc": -32700,
      "retryable": false,
      "message_key": "err.parse"
    },
    {
      "code": "INVALID_REQUEST",
      "jsonrpc": -32600,
      "retryable": false,
      "message_key": "err.invalid_request"
    },
    {
      "code": "METHOD_NOT_FOUND",
      "jsonrpc": -32601,
      "retryable": false,
      "message_key": "err.method_not_found"
    },
    {
      "code": "INVALID_PARAMS",
      "jsonrpc": -32602,
      "retryable": false,
      "message_key": "err.invalid_params"
    },
    {
      "code": "INTERNAL_ERROR",
      "jsonrpc": -32603,
      "retryable": true,
      "message_key": "err.internal"
    },
    {
      "code": "NOT_IMPLEMENTED",
      "jsonrpc": -32001,
      "retryable": false,
      "message_key": "err.not_implemented"
    },
    {
      "code": "TASK_NOT_FOUND",
      "jsonrpc": -32002,
      "retryable": false,
      "message_key": "err.task_not_found"
    },
    {
      "code": "MODEL_MISSING",
      "jsonrpc": -32003,
      "retryable": true,
      "message_key": "err.model_missing"
    },
    {
      "code": "FFMPEG_MISSING",
      "jsonrpc": -32004,
      "retryable": false,
      "message_key": "err.ffmpeg_missing"
    },
    {
      "code": "CANCELLED",
      "jsonrpc": -32005,
      "retryable": true,
      "message_key": "err.cancelled"
    },
    {
      "code": "DETECTOR_MISSING",
      "jsonrpc": -32006,
      "retryable": true,
      "message_key": "err.detector_missing"
    }
  ]
}
"""

_CONTRACT = _json.loads(_CONTRACT_JSON)

TYPES: dict = _CONTRACT['types']
METHOD_LIST: list = _CONTRACT['methods']
METHODS: dict = {method['name']: method for method in METHOD_LIST}
ERROR_CODES: dict = {entry['code']: entry for entry in _CONTRACT['error_codes']}
IMPLEMENTED_METHODS: tuple = tuple(
    method['name'] for method in METHOD_LIST if method.get('implemented')
)
