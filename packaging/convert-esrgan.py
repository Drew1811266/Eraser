"""Convert the Real-ESRGAN x4plus PyTorch weights to ONNX.

Why this exists at all: the reference implementation installs a separate Python
3.12 virtualenv, pulls torch into it, and runs a worker subprocess just to
upscale. That is a 2 GB dependency on Windows and a second interpreter to keep
alive — exactly what this port was designed to avoid. Real-ESRGAN is a plain
convolutional network, so ONNX Runtime (which the app already ships for LaMa)
can run it, provided someone produces the .onnx file.

That someone is this script. It runs once, at build time, on a developer
machine; torch is a *build* dependency and never ends up in the shipped engine.

The architecture below is BasicSR's `RRDBNet` with the exact attribute names the
checkpoint uses (`conv_first`, `body.N.rdbM.convK`, `conv_body`, `conv_up1`,
`conv_up2`, `conv_hr`, `conv_last`), which is why the state dict loads strictly
without any renaming.

    pip install torch --index-url https://download.pytorch.org/whl/cpu
    python packaging/convert-esrgan.py --weights RealESRGAN_x4plus.pth \
        --out models/real-esrgan/1.0.0/real-esrgan-x4plus.onnx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

NUM_FEATURES = 64
NUM_BLOCKS = 23
GROWTH_CHANNELS = 32
SCALE = 4


class ResidualDenseBlock(nn.Module):
    """Five densely connected 3x3 convs; the residual is scaled by 0.2."""

    def __init__(self, num_features: int = NUM_FEATURES, growth: int = GROWTH_CHANNELS) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(num_features, growth, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_features + growth, growth, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_features + 2 * growth, growth, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_features + 3 * growth, growth, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_features + 4 * growth, num_features, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    """Three residual dense blocks, each contribution scaled by 0.2."""

    def __init__(self) -> None:
        super().__init__()
        self.rdb1 = ResidualDenseBlock()
        self.rdb2 = ResidualDenseBlock()
        self.rdb3 = ResidualDenseBlock()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.rdb1(x)
        out = self.rdb2(out)
        out = self.rdb3(out)
        return out * 0.2 + x


class RRDBNet(nn.Module):
    """The generator: 23 RRDB blocks and two nearest-neighbour x2 upsamples."""

    def __init__(self) -> None:
        super().__init__()
        self.conv_first = nn.Conv2d(3, NUM_FEATURES, 3, 1, 1)
        self.body = nn.Sequential(*(RRDB() for _ in range(NUM_BLOCKS)))
        self.conv_body = nn.Conv2d(NUM_FEATURES, NUM_FEATURES, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(NUM_FEATURES, NUM_FEATURES, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(NUM_FEATURES, NUM_FEATURES, 3, 1, 1)
        self.conv_hr = nn.Conv2d(NUM_FEATURES, NUM_FEATURES, 3, 1, 1)
        self.conv_last = nn.Conv2d(NUM_FEATURES, 3, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.conv_first(x)
        body = self.conv_body(self.body(features))
        features = features + body
        features = self.lrelu(self.conv_up1(F.interpolate(features, scale_factor=2, mode="nearest")))
        features = self.lrelu(self.conv_up2(F.interpolate(features, scale_factor=2, mode="nearest")))
        return self.conv_last(self.lrelu(self.conv_hr(features)))


def load_weights(model: RRDBNet, weights: Path) -> None:
    checkpoint = torch.load(str(weights), map_location="cpu", weights_only=False)
    # Official checkpoints keep the EMA weights under `params_ema`; the plain
    # `params` entry exists only on some re-uploads.
    params = checkpoint.get("params_ema") or checkpoint.get("params") or checkpoint
    model.load_state_dict(params, strict=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", required=True, help="RealESRGAN_x4plus.pth")
    parser.add_argument("--out", required=True, help="destination .onnx")
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()

    weights = Path(args.weights)
    if not weights.is_file():
        print(f"weights not found: {weights}")
        return 2

    print(f"torch {torch.__version__}")
    model = RRDBNet()
    load_weights(model, weights)
    model.eval()

    total = sum(p.numel() for p in model.parameters())
    print(f"loaded {sum(1 for _ in model.state_dict())} tensors, {total / 1e6:.1f}M parameters")

    # A small, awkwardly sized input: the export must keep both spatial dims
    # dynamic so the runtime can tile at whatever size it likes.
    dummy = torch.zeros(1, 3, 32, 48)
    with torch.no_grad():
        reference = model(dummy)
    print(f"reference output {tuple(reference.shape)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(
            model,
            dummy,
            str(out),
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {2: "height", 3: "width"}, "output": {2: "height4", 3: "width4"}},
            opset_version=args.opset,
            do_constant_folding=True,
            # The TorchScript exporter; the newer dynamo path needs onnxscript.
            dynamo=False,
        )
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")

    # Prove the export matches the PyTorch forward pass before declaring victory.
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    for height, width in ((32, 48), (64, 80)):
        probe = np.random.RandomState(0).rand(1, 3, height, width).astype(np.float32)
        got = session.run(None, {"input": probe})[0]
        with torch.no_grad():
            want = model(torch.from_numpy(probe)).numpy()
        delta = float(np.abs(got - want).max())
        expected = (1, 3, height * SCALE, width * SCALE)
        ok = got.shape == expected and delta < 1e-3
        print(f"  {height}x{width} -> {got.shape}  max|onnx-torch|={delta:.2e}  {'OK' if ok else 'MISMATCH'}")
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
