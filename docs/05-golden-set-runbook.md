# 05 · 黄金样本采集手册（M0）

> 目标：在**动任何代码之前**，把现有 mac 版的正确行为固化成可重复比对的样本。
> 这是整个迁移期唯一的客观验收依据。耗时约半天。

---

## 1. 为什么必须现在做

重构一旦开始，你以为的"正确结果"就只存在于记忆里。后续每一次改动都需要回答同一个问题：**输出变差了吗？**

没有黄金样本时，这个问题的答案只能是"看起来差不多"——而视觉质量恰恰是最容易被"看起来差不多"骗过的东西（接缝、闪烁、色偏都是细微退化）。

---

## 2. 用例设计（6 条，覆盖失效面）

| 用例 | 输入特征 | 要覆盖的风险 |
|---|---|---|
| `case01_1080p_h264_single_roi` | 1920×1080 / H.264 / AAC / 单 ROI 在角标处 | 基线主路径 |
| `case02_multi_roi` | 同上有 **2–3 个** ROI，时间区间不同 | 多段并集、帧区间裁剪 |
| `case03_no_audio` | 无音轨 | `-map 1:a?` 缺失音轨不报错 |
| `case04_edge_roi` | ROI **越出画面右下边界** | 坐标裁剪归一化 |
| `case05_portrait` | 720×1280 竖屏 | 非 16:9 几何 |
| `case06_long_clip` | ≥60 s | 内存/临时目录/进度单调性 |

素材选取建议：优先用真实业务视频（有水印、有运动、有场景切换），而不是纯色测试图——纯色图无法暴露时序闪烁类退化。

---

## 3. 采集步骤

### 3.1 准备目录

```
golden/
  cases/
    case01_1080p_h264_single_roi/
      input.mp4
      annotations.wmr.json
      expected.mp4
      meta.json
    ...
```

### 3.2 逐条执行

对每条用例：

1. **放入 `input.mp4`**（原始素材，不改动）。
2. 用**现有 mac 版应用**导入该视频，按设计意图画好 ROI，设置帧区间。
3. 在应用里**保存标注**，随后把生成的 `<视频名>.wmr.json` 复制为 `annotations.wmr.json`。
4. 执行去水印，把输出视频复制为 `expected.mp4`。
5. 记录 `meta.json`：

```json
{
  "case_id": "case01_1080p_h264_single_roi",
  "captured_at": "2026-11-20T10:00:00+08:00",
  "captured_on": { "os": "macOS", "chip": "Apple M?", "ram_gb": 0 },
  "engine": { "model_id": "lama_roi", "params": {} },
  "input": { "width": 1920, "height": 1080, "fps": 30, "frames": 900, "has_audio": true },
  "baseline": {
    "wall_seconds": 0,
    "peak_rss_mb": 0,
    "output_bytes": 0
  },
  "notes": ""
}
```

> `baseline.wall_seconds` 是后续性能回归的分母。没有它，"变慢了"永远只是感觉。

### 3.3 采集时的三条纪律

1. **同一条用例只跑一次**，不要挑"跑得最好"的那次——黄金样本要代表典型行为，不是最优行为。
2. **记录失败**：如果某条用例在 mac 上本身就失败或明显劣化，如实记录到 `notes`。这同样是信息，且能避免 Windows 版为"复现一个本来就坏的输出"而浪费时间。
3. **不要此时优化**：发现 bug 也先记下来，不要顺手修。修了基准就变了。

---

## 4. 验收方式（不是逐比特比对）

编码器、硬件、线程调度都不同，**逐比特相等不可能也不需要**。采用两级判定：

| 级 | 方法 | 阈值 |
|---|---|---|
| 客观 | FFmpeg `ssim` / `psnr` 滤镜，逐帧平均 | SSIM ≥ **0.98**，PSNR ≥ **40 dB**（先按此执行，首轮跑完再按实际分布校正） |
| 主观 | 抽取首帧 / 中间帧 / 末帧，人工目视接缝与闪烁 | 无可察觉退化 |

配套脚本：`scripts/compare-video.py`

```bash
python scripts/compare-video.py \
  --expected golden/cases/case01_1080p_h264_single_roi/expected.mp4 \
  --actual   out/case01.mp4 \
  --ssim-min 0.98 --psnr-min 40
```

退出码 `0` 表示通过，`1` 表示低于阈值——可直接接进 CI 作为门禁。

> 依赖 ffmpeg。若本机没有，指向应用内置的那一份：
> `--ffmpeg "D:/path/to/vendor/ffmpeg/win32-x64/ffmpeg.exe"`

---

## 5. 存哪里

- **不要提交进 git**（`.gitignore` 已排除 `golden/cases/**/*.mp4`）。
- 建议放共享盘或对象存储，仓库里只保留 `input.mp4` 的来源说明与 `meta.json`。
- 若坚持入库，用 Git LFS，并注意单文件 2 GiB 上限（参考仓库已踩过这个坑）。

---

## 6. 完成标志

- [ ] 6 个用例目录齐全，`input.mp4` / `annotations.wmr.json` / `expected.mp4` / `meta.json` 四件套完整
- [ ] 每条用例的 `baseline.wall_seconds` 已填
- [ ] `compare-video.py` 用 `expected.mp4` 与自身比对，SSIM = 1.0（自检通过）
- [ ] 样本可被另一台机器复现（把样本拷到 Windows，脚本能跑）

四项全绿，才允许进入 M1。
