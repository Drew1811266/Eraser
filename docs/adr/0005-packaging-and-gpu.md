# ADR-0005 · 打包：onedir 冻结 + 双安装包（CPU / GPU）

- **状态**：**已采纳**（2026-11-21）
- **日期**：2026-11-21
- **替代**：`docs/06-execution-plan.md` D6/D7（模型"按需下载"）

---

## 背景

M4 要把引擎和外壳装成一个能双击安装、双击启动的 Windows 应用。三个问题必须一次定下来：

1. 引擎怎么变成可执行文件；
2. 模型和 ffmpeg 放哪；
3. GPU 加速怎么交付。

三者的答案互相牵制，所以要一起决定。

---

## 决策一：PyInstaller **onedir**，不是 onefile

| 方案 | 启动成本 | 杀软观感 | 备注 |
|---|---|---|---|
| **onedir**（选定） | 直接加载，约 1 秒 | 正常 | 目录 413MB，启动即用 |
| onefile | 每次启动解压约 400MB 到 `%TEMP%` | 差（大量写临时目录 + 自解压行为） | 首启 5–10 秒 |

`console=False`：引擎只走 stdio，不弹黑框；日志照旧写 stderr，被 Electron 捕获。
`engine/entrypoint.py` 是唯一入口，它同时承担 CLI 角色（`process`/`probe`/`info`），
保证"冻结后的二进制"和"开发时的 python -m"走同一条代码路径。

## 决策二：模型与 ffmpeg **随包**，不按需下载

原计划 D7 是"模型按需下载"。放弃它的理由是**验收**而不是懒：

- `model.install` / `model.installStatus` 至今仍是 `NOT_IMPLEMENTED`，也就是说应用没有下载能力；
- 一个装好却必须先联网拉 198MB 才能用的去水印工具，比大 200MB 的安装包糟糕得多；
- 目标机器的网络环境（代理、企业网）不可控，首启失败会被当成"软件坏了"。

代价是安装包体积。实测：CPU 版 **601MB**，其中 ffmpeg 345MB + 模型 208MB + 引擎 413MB（压缩后有重叠）。

## 决策三：GPU 加速做成**第二个安装包**

CUDA 13 运行时的实测体积：

| 组件 | 体积 |
|---|---|
| cuBLAS（`cuda13/bin/x86_64`） | 771MB |
| cuDNN 9（`cudnn/bin`） | 603MB |
| nvrtc | 187MB（不需要，LaMa 图不 JIT） |
| **实际需要** | **约 1.16GB** |

把它塞进默认安装包，等于让所有人为一个只有 NVIDIA 机器受益的东西多付 1.3GB 下载量。
所以产出两个包，**应用本体完全相同**：

```
Eraser-Setup-<v>.exe        CPU   601MB
Eraser-Setup-GPU-<v>.exe    +CUDA 约 2.3GB
```

GPU 版的差异只有一个目录：`resources/engine/cuda/*.dll`（平铺）。

运行时如何找到它：PyInstaller 冻结后，`onnxruntime` 无法通过 `sys.path` 找到 `nvidia-*` 包里的 DLL，
而 ONNX Runtime 用 `LoadLibraryEx(..., LOAD_WITH_ALTERED_SEARCH_PATH)` 加载 provider，
`os.add_dll_directory` 对它无效。`engine/platform/ort_runtime.py` 因此做了两件事：
把 `engine/cuda` 加入 `PATH`，并调用 `os.add_dll_directory`；任一机制生效即可。
找不到就**如实报告 `cuda_usable: false`**，而不是假装有 GPU 再默默跑 CPU。

实测（48 帧 640×360 合成片，含解码/编码/转码）：

| 构建 | 执行 | 耗时 | 每帧 |
|---|---|---|---|
| 冻结 CPU | CPUExecutionProvider | 66.7s | 1390ms |
| 冻结 GPU | CUDAExecutionProvider | **9.1s** | 190ms |

---

## 后果

- **正面**：装完即用；无 Python、无 conda、无网络依赖；GPU 用户拿 7.3× 端到端加速；CPU 用户不必为 CUDA 付流量。
- **负面**：两个安装包要分别构建与验证；CPU 版对 1080p 长片偏慢（约 1.4s/帧，且只有被标注区间内的帧会真正推理）。
- **签名**：没有 Authenticode 证书，安装与首次运行会触发 SmartScreen 提示。这是 `04-风险登记` 里已接受的风险，发布说明中明示。
- **体积优化空间**：`onnxruntime-gpu` 的 CPU 版 DLL 与 CUDA provider DLL 在 CPU 包里是冗余的；若将来要压到 200MB 内，可换成 `onnxruntime` CPU wheel 单独构建。
