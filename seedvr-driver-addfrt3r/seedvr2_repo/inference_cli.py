"""Stand-in for the upstream inference CLI: same contract, no model."""
import argparse, shutil, subprocess, sys, time

parser = argparse.ArgumentParser()
parser.add_argument("--output")
parser.add_argument("--model_dir")
parser.add_argument("--dit_model")
parser.add_argument("--resolution", type=int, default=1080)
parser.add_argument("--batch_size")
parser.add_argument("--chunk_size")
parser.add_argument("--temporal_overlap")
parser.add_argument("--max_resolution")
parser.add_argument("--input_noise_scale")
parser.add_argument("--latent_noise_scale")
parser.add_argument("--video_backend")
parser.add_argument("--dit_offload_device")
parser.add_argument("--vae_offload_device")
parser.add_argument("--tensor_offload_device")
parser.add_argument("input")
args, _unknown = parser.parse_known_args()

print("PROGRESS stage=warmup step=vae_encode", flush=True)
time.sleep(0.2)
mode = __import__("os").environ.get("STUB_MODE", "ok")

for index in range(1, 6):
    time.sleep(0.15)
    print(f"PROGRESS chunk={index}/5", flush=True)

if mode == "fail":
    print("Traceback: simulated CUDA out of memory", flush=True)
    sys.exit(3)
if mode == "silent":
    sys.exit(0)

target = __import__("os").path.join(args.output, "stub_seedvr.mp4")
subprocess.run(['D:\\code project\\Eraser\\vendor\\ffmpeg\\win32-x86_64\\ffmpeg.exe', "-y", "-v", "error", "-i", args.input, "-c", "copy", target], check=True)
print("wrote", target, flush=True)
