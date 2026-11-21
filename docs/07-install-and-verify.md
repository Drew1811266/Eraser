# 07 · 安装与验收

面向拿到安装包的人。想改代码请回到 [README](../README.md)。

---

## 1. 选哪个安装包

| 安装包 | 体积 | 适用 |
|---|---|---|
| `Eraser-Setup-<版本>.exe` | 约 600MB | 所有 Windows 机器。CPU 推理，能用但慢 |
| `Eraser-Setup-GPU-<版本>.exe` | 约 2.3GB | 有 NVIDIA 显卡的机器。多带 1.16GB CUDA 运行时，快 7 倍 |

两个包装出来的应用**完全一样**，区别只是 GPU 包里多一个 `engine/cuda/` 目录。
装完可以互换：把 `cuda` 目录放进去/拿出来即可升级/降级，不需要重装。

**怎么选**：显卡是 NVIDIA（GTX 10 系及以上，驱动 ≥ 570）就装 GPU 版；
否则装 CPU 版。拿不准就先装 CPU 版，跑一段 30 秒的片子试试手感。

## 2. 系统要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 1809+ / Windows 11，64 位 |
| 内存 | 8GB（GPU 版建议 16GB） |
| 磁盘 | 安装 3GB；处理时还需要临时空间（约为片长的 1–2 倍） |
| 显卡 | GPU 版需 NVIDIA，显存 ≥ 4GB；不支持 AMD/Intel 加速（会退回 CPU） |
| 依赖 | 无需安装 Python、ffmpeg、CUDA 工具包——都已随包 |

## 3. 不安装也能运行

安装包做的事只是把 `win-unpacked` 整个目录复制到 `%LOCALAPPDATA%\Programs\Eraser`
并登记卸载项。**同一个程序有三种打开方式**，功能完全一致：

| 方式 | 操作 | 需要安装吗 |
|---|---|---|
| 免安装直接跑 | 双击 `dist/installer/win-unpacked/Eraser.exe` | 不需要 |
| 用构建产物启动 | `npm --prefix desktop start` | 不需要（需先 `npm install` + `npm run build`） |
| 开发模式 | `npm --prefix desktop run dev` | 不需要，界面改动即时生效 |

## 4. 安装

1. 双击安装包。
2. Windows 会弹 **"Windows 已保护你的电脑"**：因为没有做代码签名（无证书），
   点「更多信息」→「仍要运行」。这是预期行为，见 `04-风险登记` R7。
3. 选择安装位置（默认当前用户目录，不需要管理员权限）。
4. 安装完成后勾选的「运行 Eraser」会直接启动。

卸载：设置 → 应用 → Eraser，或安装目录下的 `Uninstall Eraser.exe`。
默认**保留**用户数据（见下）。

## 5. 第一次跑

1. 点「打开视频」，选一个文件。
2. 预览区右上角会出现一个虚线框，那是默认猜测的水印位置；
   **在画面上拖动鼠标重新框选**，只框住水印本身，不要带太多背景。
3. 「保存到」默认是 `下载/Eraser`，可改。
4. 点「开始擦除」。进度条会显示阶段、帧数、预计剩余时间，可随时取消。
5. 完成后可以直接「播放」或「打开所在文件夹」。

结果文件名为 `<原文件名>_no_watermark.mp4`，H.264 + 原音轨。

## 6. 不用界面也能验证

引擎本身就是 CLI， froze 前后同一条代码路径：

```powershell
# 安装目录下（默认 %LOCALAPPDATA%\Programs\Eraser\resources\engine）
.\eraser-engine.exe info
.\eraser-engine.exe probe "D:\videos\clip.mp4"
.\eraser-engine.exe process "D:\videos\clip.mp4" --rect 480,20,120,60 --output "D:\out\clip.mp4"
```

`info` 会打印它实际能用的执行器：

```
runtime     {'available_providers': [...], 'cuda_usable': True, 'onnxruntime': '1.32.0'}
devices:
  cuda  NVIDIA GeForce RTX 5090 D v2  24455 MB  (usable)
  cpu   CPU (32 logical cores)
```

`cuda_usable: False` 就是真的没用上 GPU——不是"应该能"，是实测加载了 provider DLL。

## 7. 数据放在哪

| 内容 | 位置 |
|---|---|
| 程序 | `%LOCALAPPDATA%\Programs\Eraser\` |
| 用户数据 | `%LOCALAPPDATA%\Eraser\`（日志在 `logs/`） |
| 默认输出 | `%USERPROFILE%\Downloads\Eraser\` |
| 临时帧 | `%LOCALAPPDATA%\Eraser\work\` ，正常结束会清理 |

卸载不会删 `%LOCALAPPDATA%\Eraser`；要彻底清理请手动删除该目录。

## 8. 已知限制（v1）

| 限制 | 说明 |
|---|---|
| 一次只能框一个区域 | 同一时间段的多个水印要分两次处理 |
| 无代码签名 | SmartScreen 会拦一次，见上 |
| 不支持 AMD/Intel 加速 | 会走 CPU；逻辑正确，只是慢 |
| 长片偏慢 | 只有被标注区间内的帧会真正推理，其余帧直接拷贝 |
| 无自动更新 | 新版本需手动安装 |
| 预览不支持 MKV/部分 HEVC | Chromium 的解码限制；处理本身不受影响（ffmpeg 负责） |

## 9. 打不开或一闪而过

先看日志：`%APPDATA%\Eraser\logs\main.log`（便携运行时在程序目录旁的
`userData` 里）。启动的每个阶段都会写一行，崩溃前最后一行就是线索。

两种已知的"不是程序问题"：

| 现象 | 原因 | 处理 |
|---|---|---|
| 双击后什么都不出现，日志也只有一行 | 环境变量 `NODE_USE_ENV_PROXY` 被设置，Electron 的渲染进程起不来 | `set NODE_USE_ENV_PROXY=` 后重试 |
| 同上，且 `%APPDATA%` 不可写 | Chromium 无法创建 profile | 指定可写目录：`Eraser.exe --user-data-dir=D:\temp\eraser` |

## 10. 出问题怎么报

引擎日志在应用里点右上角「日志」就能看，也可以在 `%LOCALAPPDATA%\Eraser\logs\` 找到。
报问题时请附上：

1. `eraser-engine.exe info` 的完整输出；
2. 出问题的那段日志；
3. 视频的 `probe` 输出（分辨率、帧率、编码）。
