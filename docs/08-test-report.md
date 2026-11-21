# 08 · 测试报告（v0.1.0）

- **日期**：2026-11-21
- **被测对象**：`dist/installer/Eraser-Setup-0.1.0.exe` / `…-GPU-0.1.0.exe`
- **结论**：**8 道门禁、256 项检查全部通过**；两个安装包的载荷均可跑出成品。

---

## 1. 怎么跑

```bash
bash scripts/check.sh --full
```

八道门禁依次执行，任一失败即中止。也可以单独跑：

| 门禁 | 命令 | 需要 |
|---|---|---|
| 2 | `.venv/Scripts/python.exe -m unittest discover -s tests -t .` | Python venv |
| 3 | `node scripts/e2e-engine.mjs` | 仅 Node |
| 4 | `npm --prefix desktop run check` | `npm install` |
| 5 | `.venv/Scripts/python.exe scripts/e2e-video.py` | venv + ffmpeg + 模型 |
| 6 | `.venv/Scripts/python.exe scripts/e2e-upscale.py` | 同上 + Real-ESRGAN |
| 7 | `node scripts/e2e-renderer.mjs` | 构建产物 + Playwright chromium |
| 8 | `.venv/Scripts/python.exe scripts/e2e-installer.py` | 7-Zip + 已产出的安装包 |

---

## 2. 结果

| # | 门禁 | 数量 | 结果 |
|---|---|---|---|
| 1 | 契约产物与 `contract/` 无漂移 | 1 | ✅ |
| 2 | 引擎单测（平台层、契约、标注、任务、分块拼接） | 113 | ✅ |
| 3 | 引擎 ↔ 外壳 JSON-RPC（噪声、崩溃、超时、并发请求） | 31 | ✅ |
| 4 | 外壳类型检查 + 界面/IPC 单测 | 31 | ✅ |
| 5 | 真实视频端到端 | 41 | ✅ |
| 6 | Real-ESRGAN 超分端到端 | 5 | ✅ |
| 7 | 构建产物在真实浏览器 + 真实引擎 | 18 | ✅ |
| 8 | 两个安装包的载荷与包内引擎（含包比代码旧的守卫） | 17 | ✅ |

### 各门禁测什么

**引擎单测**：平台路径（`%LOCALAPPDATA%`、长路径、临时目录）、ffmpeg 定位、原子写与占用重试、
标注归一化（与参考实现逐条对齐）、契约结构与错误码、任务生命周期、Real-ESRGAN 分块拼接。

**协议端到端**：真的起一个引擎进程，用 Electron 侧同一份 `EngineClient` 通信；覆盖并发请求、
2000 行 stderr 噪声不污染协议、引擎崩溃时请求以 `ENGINE_EXITED` 失败而不是挂起。

**视频端到端**（41 项，最重的一道）：
- 合成 640×360 / 48 帧片段，已知水印位置 → 真实推理 → 逐帧核对：
  水印区域平均改变 68–81/255、水印色距 93–125、遮罩外平均差 2.25、分辨率/帧率/帧数不变；
- 进度通知 64 条、单调、含中间值，帧计数到 48；
- 竖屏 360×640 **带音轨**片段：几何/帧数不变，**音轨仍在**；
- 运行中取消：2.6 秒返回，状态 `cancelled`，**不留半成品文件**；
- 坏输入（文本文件改名 .mp4）：probe 与 process 都明确失败并带原因，不挂起；
- `测试 视频 (1).mp4` 这类含空格/中文的文件名可正常处理；
- 两个任务同时提交，都成功。

**超分端到端**：640×360 → 2560×1440；帧数不变；缩回原尺寸后平均差 1.23/255；
Laplacian 能量 0.4 vs bicubic 0.2（确实更锐，不是单纯的放大）。

**真实浏览器 UI**（18 项）：加载的是 `desktop/dist-renderer` 的**构建产物**，`window.eraser`
接到真实引擎的真实 JSON-RPC 上。覆盖：界面挂载、引擎徽标、probe 元信息渲染、预览 URL 使用
自定义协议、进度有中间值、结果路径与磁盘文件一致、输出几何与帧数正确、结果再超分到 2×、
取消在界面上得到反馈、无未捕获页面错误。

**安装包验收**（17 项）：用 7-Zip 解开两个 NSIS 包，校验载荷完整（Eraser.exe / app.asar /
引擎 / 两个模型 / ffmpeg），确认 CPU 包**不含** CUDA 而 GPU 包**含** cudart/cublas/cudnn，
然后**用包内的引擎**真实处理一段视频并核对输出几何与帧数。

---

## 3. 本轮发现并修复的缺陷

| 缺陷 | 影响 | 怎么发现的 |
|---|---|---|
| 任务完成通知不带 `result` | 进度到 100% 后界面永远不显示结果（外壳收到 success 就停止轮询） | 真实浏览器端到端 |
| 2× 超分上报 4× 尺寸 | 文件正确但界面显示错误分辨率 | 打包后手动跑 `--scale 2` |
| 进度回调签名不匹配 | 进度条恒为 0% | CLI 首次运行 |

前两个都属于"不抛异常、只是行为不对"，只有真跑才能发现。

---

## 4. 无法在本机执行的部分

| 项 | 原因 | 替代 |
|---|---|---|
| ~~Electron 真实窗口~~ | **已解决**：真正的原因是环境变量 `NODE_USE_ENV_PROXY=1`（会让 Electron 内嵌的 Node 改网络栈，渲染进程在首个页面前就崩）与 `%APPDATA%` 不可写（Chromium 无法建 profile）。去掉前者、把 profile 指到可写目录后，真实应用完整跑通 | 现在是第 8 道门禁：`node scripts/e2e-desktop.mjs`，11 项检查 |
| NSIS 安装向导 | 同上，需要交互式桌面；`/S` 静默安装在此环境直接退出 | `scripts/e2e-installer.py`（解包 → 校验 → 跑包内引擎） |
| 真机首次安装的 SmartScreen | 无证书，属已知且已接受的风险（`04-风险登记` R7） | 文档写明操作步骤 |
| mac 黄金样本对比 | 需要在原 mac 机器上录制（`05-golden-set-runbook`） | 合成夹具 + 数值断言 + 分块/自一致性检查 |

**建议**：在交互式桌面上补跑一次

```bash
node scripts/e2e-desktop.mjs
```

它才会真正创建 BrowserWindow、点「打开视频」、跑完整段并读回结果——这是唯一还没被覆盖的链路。

---

## 5. 残余风险

| 风险 | 等级 | 说明 |
|---|---|---|
| 真实素材上的去水印质量 | 中 | 合成夹具无法证明真人像/纹理上的融合质量；参考实现的第二轮质量门在夹具上 48/48 被拒（回退到第一轮），真实素材上表现未知 |
| Electron 45 在用户机器上的启动 | 低 | 本机崩溃原因未定位到产品代码（最小复现同样崩溃），但也不能排除与特定 Windows 构建有关 |
| 未签名 | 已接受 | 每次安装都会触发 SmartScreen 提示 |
| 长时间/大分辨率视频 | 中 | 未测 4K 长片；内存与临时磁盘占用按"帧流式管道"设计，但没有实测上限 |


## 9. 与参考实现的逐像素对比（原代码生成参考集）

mac 黄金样本没有，但**参考实现本身可以在 Windows 上跑**：`src/core/video_processor.py`
+ `remover.py` 装上 PyTorch（复用 SeedVR2 运行时）和 `simple-lama-inpainting` 即可。
于是可以拿同一段素材、同一份标注，跑两边，再逐帧比。

`scripts/parity-removal.py`（门禁 14）实测：

```
SSIM = 0.9947   PSNR = 47.69 dB   阈值 0.98 / 40
```

差异来源是确定的：参考实现用 TorchScript 版 LaMa、本移植用 ONNX 版，两边
推理后端不同，本来就不可能逐比特相同。

**这意味着**：去水印这条主链路已经有了可重复的、对原实现的数值对比，
不再只有"看起来差不多"。

## 10. 黄金样本现状

`scripts/golden.py` 已可执行手册里的流程。当前仓库里只有 **3 条合成 baseline 用例**
（单 ROI / 越界 ROI / 竖屏带音轨），它们的作用是防退化，**不能证明与 mac 版一致**。

参考实现代码里的 `src/core/scene_splitter.py` 是纯逻辑（ffmpeg + 可选 scenedetect），
可用于逐函数对比；`annotations.py` 只依赖标准库，也可以直接跑。宏观画质对位仍需要
一台 mac 录 reference 用例。


## 10. SeedVR2 现状（可验证到哪一步）

| 环节 | 状态 | 证据 |
|---|---|---|
| 能力探测与原因 | 已验证 | 14 个单测；`upscale.capabilities` 6 项协议检查 |
| 安装器（venv / 固定 commit / 校验下载 / 取消 / 互斥锁） | 已验证 | 13 个单测（含 sha256 不符、取消、锁） |
| 驱动（命令拼装 / 进度解析 / 取消 / 出片发现 / 任务接线） | 已验证 | `scripts/e2e-seedvr-driver.py` 13 项，对桩 CLI |
| **模型本身出片** | **已验证（GPU）** | `scripts/e2e-seedvr-real.py` 11 项：真实权重出 1920×1080，帧数/音轨保留，进度来自 CLI 自身阶段 |

实测数据（RTX 5090 D v2，Q4_K_M）：4 帧 320×180 → 1080p 用时 14s；单次 2 帧 256×144 用时 7–8s。


安装器已实测到这里：Python 3.12 发现、venv 创建、固定 commit 拉取解包（20MB）都成功；
卡在依赖安装（torch 约 2.5GB）。换一台能正常访问 PyPI 的机器执行即可继续：

```bash
python -c "from engine.pipeline.seedvr.runtime import install; install('seedvr2_3b_q4_k_m_gguf', progress=print)"
```

### 接进 Windows 时踩到的三个坑（都已修）

1. **GBK 编码崩溃**：上游 CLI 打印 `⚠️` 等 emoji，中文 Windows 控制台是 GBK，
   进程在 import 阶段就 `UnicodeEncodeError` 退出。修复：给子进程强制
   `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`。
2. **找不到 ffmpeg**：CLI 要求 ffmpeg 在 PATH 上。修复：把引擎自带的 ffmpeg 目录
   注入子进程 PATH，不要求用户另装。
3. **cu126 没有 Blackwell 内核**：`torch 2.14.1+cu126` 在 5090（sm_120）上装得上、
   一跑就报 `no kernel image is available`。修复：CUDA 轮子默认走 cu128，
   并用 `--reinstall-package` 确保真的替换掉 CPU 轮子。

还有一个货真价实的 bug：`reader` 变量名和后面线程的 `reader` 撞车，
进度线程一启动就死，子进程写满管道后整次推理挂死——桩测试没覆盖到，真实模型一跑就现形。
