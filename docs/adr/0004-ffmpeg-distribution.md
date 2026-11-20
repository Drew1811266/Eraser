# ADR-0004 · ffmpeg 发行版：必须用 `gpl` 构建，`lgpl` 不可行

- **状态**：**已采纳**（2026-11-20）
- **日期**：2026-11-20
- **替代**：`docs/06-execution-plan.md` D4（原定 BtbN `win64-lgpl`）

---

## 背景

`06-execution-plan.md` 的 D4 原本锁定 **BtbN `win64-lgpl`** 静态构建，理由是 LGPL 分发的法律负担最小。资产按此下载并接入了 `engine/platform/ffmpeg.py`。

M2.7 端到端验证时，用合成的 640×360 测试片第一次真正跑通全链路，编码阶段直接失败：

```
[vost#0:0] Unknown encoder 'libx264'
Error opening output file.
```

`ffmpeg -version` 的 configuration 行给出了原因：

```
--disable-libx264 --disable-libx265 --disable-libxvid
```

**libx264 是 GPL 代码**（x264 项目本身就是 GPL），所以任何 `--enable-gpl` 的构建才可能带它。BtbN 的 `lgpl` 变体把 `--enable-gpl` 关掉，libx264 随之消失。

这不是可选的编解码器偏好问题：移植过来的管线在两处硬编码了 `libx264`（`_transcode_video_h264` 与带音频的 `_render_h264_with_audio`，`-preset medium`、`-pix_fmt yuv420p`、`-movflags +faststart`）。没有它，**处理完成的视频无法写出**，产品核心功能为零。

---

## 评估

| 方案 | 保真度 | 质量 | 法律负担 | 工作量 |
|---|---|---|---|---|
| **BtbN `win64-gpl` 静态构建** | 与 mac 版完全一致（同一编码器、同一参数） | 最好 | 分发内容含 GPL 二进制；随包附 `LICENSE.txt` + 源码获取地址 | 0（换二进制即可） |
| 改用 `libopenh264`（BSD，本构建已启用） | 中：需把 `-preset/-crf` 改写成码率模式 | 中 | 无 | 小，但改变了输出特征 |
| 改用 `h264_mf`（Windows Media Foundation） | 低：参数语义完全不同，跨驱动行为不一 | 依驱动 | 无 | 中，且难以验证 |
| 改用 `libx265` / VP9 | 低：容器/兼容性变化 | 好 | GPL / BSD | 中，且播放器兼容性下降 |

关键判断：**"最大化 LGPL 收益"与"保持移植保真度"冲突时，选择保真度。** 本项目的验收标准是"与 mac 版功能近乎一致"，而编码器是输出链路的一部分；在没有 mac 黄金样本的前提下，任何编码器替换都属于"无法验证的算法改动"，被 M2 的机械改动约束明确排除。

法律上，ffmpeg 是以**独立进程**方式被调用的（`subprocess.run([...])`），不链接、不派发其内部符号，因此 GPL 的传染性只作用于 ffmpeg 二进制本身；义务是随分发内容提供许可证文本与源码获取方式。

---

## 决策

1. 使用 **BtbN `ffmpeg-master-latest-win64-gpl.zip`** 的 `ffmpeg.exe` / `ffprobe.exe`，放在 `vendor/ffmpeg/win32-x86_64/`。
2. 归档内的 `LICENSE.txt` 一并放到 `vendor/ffmpeg/LICENSE.txt`，随安装包分发；发布说明中给出 BtbN 构建与 FFmpeg 源码地址。
3. 管线代码**不改动**：继续使用 `libx264 -preset medium`。
4. `scripts/fetch-assets.py` 与文档中的资产来源改为 `gpl` 变体，并记录此 ADR 的依据。

---

## 后果

- **正面**：编码路径与 mac 版逐参数一致；`libx264`/`libx265`/`libvpx`/`libsvtav1` 全部可用，后续若要做"导出体积优化"有空间。
- **负面**：安装包内含 GPL 二进制，需在 `README`/关于页明示；若将来要闭源分发，需重新评估（只能换成 `libopenh264` + 码率模式，或改为链接系统 ffmpeg）。
- **体积**：`ffmpeg.exe` + `ffprobe.exe` 各约 165MB（静态构建）；PyInstaller 打包含入后仍是同量级，属已知成本。
