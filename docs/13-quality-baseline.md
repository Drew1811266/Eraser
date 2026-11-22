# 13 · 质量基线（M2 真实检测器）

- **日期**：2026-11-22
- **检测器**：`microsoft/Florence-2-base`，版本 `5ca5edf5bd017b9919c05d08aebef5e4c7ac3bac`，
  任务 `<OPEN_VOCABULARY_DETECTION>` + 提示词 `watermark`
- **运行时**：`models/florence2/venv`（专用 venv，torch cu128 + `transformers==4.46.3` + timm + einops），
  `phase=ready`、`cuda=true`；引擎 venv 不含 torch 的新依赖
- **机器**：RTX 5090 D v2 / 24 GB，Windows 11
- **素材**：`scripts/make-analysis-fixtures.py` 合成的 7 组 640×360 / 10fps / 100 帧片段，
  真值由「带水印帧 − 干净帧」逐像素差分得到（不是手写坐标）

---

## 1. 结果

| 用例 | tracks（预测/真值） | motion | 区间（预测/真值） | 命中率 | 覆盖率 | 误报帧 | 耗时 | 判定 |
|---|---|---|---|---|---|---|---|---|
| `corner_static` 右下角文字 | 1 / 1 | static | 1 / 1 | 1.00 | 0.96 | 0.00 | 6.47s | ✅ |
| `center_logo` 中心大字 | 1 / 1 | static | 1 / 1 | 1.00 | 0.96 | 0.00 | 6.53s | ✅ |
| `small_mark` 22px 小字 | 1 / 1 | static | 1 / 1 | 1.00 | 0.96 | 0.00 | 6.39s | ✅ |
| `moving_mark` 每帧 4px 右移 | 1 / 1 | **moving** | 1 / 1 | 1.00 | 0.96 | 0.00 | 6.45s | ✅ |
| `blink` 出现→消失→再现 | 1 / 1 | static | **2 / 2** | 1.00 | 0.90 | 0.04 | 6.62s | ✅ |
| `multi_shot` 三镜头同一角标 | 1 / 1 | static | **3 / 3** | 1.00 | 0.96 | 0.00 | 6.60s | ✅ |
| `clean` 无水印 | 0 / 0 | — | 0 / 0 | 1.00 | 1.00 | 0.00 | 6.42s | ✅ |

**7/7 通过。**

## 2. 校准：指标本身是对的

同一套素材换成「用真值回放的脚本检测器」（`--detector stub`），同样 **7/7 通过**。
这一步的意义：如果指标或管线有错，校准跑不满分；校准满分之后，上面的数字才可以归因于模型。

## 3. 耗时

| 场景 | 结果 |
|---|---|
| GPU 单条 100 帧片段（20 个采样点） | ~6.4s，其中约 5s 是 worker 启动 + 模型加载 |
| GPU 推理（加载完成后） | 约 0.05s/帧（batch=8，num_beams=1） |
| CPU 单帧（1280×720，spike 实测） | 约 1.0s/帧 |

模型加载占了单条片段耗时的大头，因为每个分析任务起一个 worker 进程。30 秒视频（60 个采样点）
预计 8–10s；更长的视频建议先把 worker 做成常驻进程（见 §5）。

## 4. 这组数字能证明什么

- **零样本可用**：Florence-2 没有针对本项目微调，就能找到 22px 的文字水印，
  且在水印与背景对比度不高（半透明白字 + 深色 box）时仍然命中。
- **整幅误报被挡住**：无水印素材上模型返回 99.6% 的整幅框，`max_area_ratio=0.25`
  把它过滤掉，分析结果是 `watermarks: []` + `no_watermark`，而不是一个假水印。
  这条过滤不是优化，是正确性前提。
- **时序规则成立**：移动水印判 `moving`；出现→消失→再现得到 **1 个水印 2 段区间**；
  跨 3 个镜头保持 1 个水印、3 段区间、3 个待处理片段。
- **端到端链路成立**：安装 → 运行时 → worker → 分析 → 建议，全部走真实代码路径。

## 5. 不能证明什么 / 已知风险

- **真实素材**：夹具是合成画面。压缩噪点、渐变背景、半透明图形 logo、10px 以下小字、
  与背景同色的水印都未覆盖。
- **非文字水印**：评测集只有文字；图形 logo（尤其半透明）的召回未知。
- **模型加载成本**：每个分析任务重新加载模型；长视频需要常驻 worker 或进程池。
- **transformers 版本**：必须 `<5`（5.x 让 Florence-2 的 remote code 崩在
  `forced_bos_token_id`）。运行时已钉死 4.46.3，升级前必须重跑本评测。
- **失败模式**：模型被要求"找水印"时会硬找一个；目前靠面积过滤兜底，
  若将来放宽阈值，`clean` 用例必须重跑。

## 6. 复现

```bash
python scripts/make-analysis-fixtures.py --out .cache/analysis
export ERASER_USER_DATA_DIR="$(cygpath -w "$PWD")" ERASER_TEMP_DIR="$(cygpath -w "$PWD/.cache/tmp")"
.venv/Scripts/python.exe scripts/eval-analysis.py --cases .cache/analysis --detector stub      # 校准
.venv/Scripts/python.exe scripts/eval-analysis.py --cases .cache/analysis --detector florence2  # 实测
```

首轮结果存档在 `golden/analysis/florence2.json`，作为后续模型/参数变更的对照基线。
