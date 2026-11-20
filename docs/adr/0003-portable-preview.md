# ADR-0003 · 视频预览：Chromium `<video>` 为主，帧服务兜底

- **状态**：建议采纳
- **日期**：2026-11-19
- **关联**：`src/gui/native_player.py`（被替代对象）

---

## 背景

mac 版的预览依赖 `native_player.py`：用 `pyobjc` 调 **AVFoundation/AVKit**，开一个原生 `AVPlayerView` 窗口，支持"原始视频 / 处理后视频"双角色，并提供 `play/pause/seek/state` 接口。这是全仓**唯一真正无法在 Windows 运行**的功能模块。

Windows 上的等价物有几种，但没有一个能 1:1 复制 AVPlayer 的行为。

---

## 评估

| 方案 | 容器/编码覆盖 | 音画同步 | 实现成本 | 风险 |
|---|---|---|---|---|
| **Chromium `<video>`** | H.264/AAC、VP8/9、AV1 良好；**MKV/AVI/部分 HEVC 不支持** | 浏览器原生，良好 | 低 | 用户导入 MKV 时黑屏 |
| **帧服务（ffmpeg 解码 → JPEG）** | **全覆盖**（ffmpeg 能解就能看） | 无音频，需自建时钟 | 中（仓库已有 `preview.ts` / `open_video_preview_session`） | 拖动卡顿；CPU 占用 |
| mpv / libmpv 嵌入 | 全覆盖 | 良好 | 高（`--wid` 窗口嵌入在 Electron 下脆弱） | 打包体积 + 平台脆弱性 |
| 自研 Media Foundation 原生插件 | 依赖系统解码器 | 良好 | 很高 | 等价于重写 `native_player.py` |

---

## 决策

**双通道自适应：探测 → 路由。**

```
导入视频
  └─ probe(codec/container)
       ├─ 浏览器可直放 (mp4/mov + h264/hevc*)
       │     → <video> 主通道：拖动、逐帧、音画同步
       └─ 浏览器不支持 (mkv/avi/部分 hevc/av1-in-mkv)
             → 帧服务通道：ffmpeg 解码 + 按需 JPEG
                 并在 UI 明确提示"此格式为逐帧预览模式"
```

补充规则：

1. **双角色对比**（原始 vs 处理后）用**两个媒体元素 + 同一时钟源**实现，`requestVideoFrameCallback` 对齐，不使用原生窗口。这样在 Windows/mac 上行为一致。
2. 逐帧步进（frame step）统一走帧服务，即使主通道是 `<video>`——因为 `<video>` 无法精确定位到帧。
3. `native_player.py` 在 Eraser 中**不迁移**，其 5 个 API 方法（`open/play/pause/seek/state`）映射为渲染进程内部状态，不再跨进程。

> `*` HEVC 在 Electron 中的支持取决于构建选项与系统；必须实测后再决定归属哪条通道，不能假设。

---

## 后果

- 少一个原生依赖（`pyobjc` 链彻底移除），Windows 打包干净。
- 需要处理"同一视频两种预览模式"的 UI 一致性，属于产品细节而非架构风险。
- 帧服务通道是既有能力（Python 与 Electron 两侧都有实现），**复用而非新写**。
- 若后续确认 HEVC 覆盖率不足，可再评估引入 mpv，属于增量优化，不影响主架构。
