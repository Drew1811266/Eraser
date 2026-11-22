# 13 · 质量基线（M2 真实检测器）

- **日期**：2026-11-22
- **检测器**：`microsoft/Florence-2-base`，版本 `5ca5edf5bd017b9919c05d08aebef5e4c7ac3bac`，
  任务 `<OPEN_VOCABULARY_DETECTION>` + 提示词 `watermark`
- **运行时**：`models/florence2/venv`（专用 venv），`phase=ready`；实际安装的
  torch / transformers / timm 版本由安装器写入 `state.json` 的 `versions` 字段
- **机器**：RTX 5090 D v2 / 24 GB，Windows 11
- **素材**：`scripts/make-analysis-fixtures.py` 合成的 8 组 10fps / 100 帧片段
  （7 组 640×360 + 1 组 1920×1080），真值由「带水印帧 − 干净帧」逐像素差分得到

---

## 1. 结果

| 用例 | tracks（预测/真值） | motion | 区间（预测/真值） | 探针 | 命中率 | 覆盖率 | 误报帧 | 耗时 | 判定 |
|---|---|---|---|---|---|---|---|---|---|
| `corner_static` 右下角文字 | 1 / 1 | static | 1 / 1 | 20 | 1.00 | 0.96 | 0.00 | 6.76s | ✅ |
| `center_logo` 中心大字 | 1 / 1 | static | 1 / 1 | 20 | 1.00 | 0.96 | 0.00 | 6.75s | ✅ |
| `small_mark` 22px 小字 | 1 / 1 | static | 1 / 1 | 20 | 1.00 | 0.96 | 0.00 | 6.77s | ✅ |
| `moving_mark` 每帧 4px 右移 | 1 / 1 | **moving** | 1 / 1 | 20 | 1.00 | 0.96 | 0.00 | 6.75s | ✅ |
| `blink` 出现→消失→再现 | 1 / 1 | static | **2 / 2** | 11 | 1.00 | 0.90 | **0.04** | 6.78s | ✅ |
| `multi_shot` 三镜头同一角标 | 1 / 1 | static | **3 / 3** | 20 | 1.00 | 0.96 | 0.00 | 6.74s | ✅ |
| `fullhd_corner` 1920×1080 角标 | 1 / 1 | static | 1 / 1 | 20 | 1.00 | 0.96 | 0.00 | 7.03s | ✅ |
| `clean` 无水印 | 0 / 0 | — | 0 / 0 | 0 | n/a | 1.00 | 0.00 | 6.45s | ✅ |

**8/8 通过。**

`fullhd_corner` 是唯一宽于 1280 分析上限的用例，因此是唯一真正走
`rescale_detections`（1.5×）的用例：检测框在 1280 宽的采样帧里产生，回到源视频坐标后
仍与 1920 宽的真值对齐。

## 2. 校准：证明的是"管线不吞框"，不是"指标不会骗人"

同一套素材用 `--detector stub` 回放真值（按采样比例缩放到检测器坐标系），同样 **8/8**。
它能证明：采样 → 坐标回映射 → 关联 → 区间 → 关键帧这条链路不会丢框或错位。

它**不能**证明指标本身是对的——回放和打分用的是同一份 `rects`。
指标的可证伪性由负样本单元测试覆盖（`tests/test_eval_analysis.py`：错位框、缺帧、
错 motion、错区间数都必须判 FAIL）。

## 3. 耗时

| 场景 | 结果 |
|---|---|
| GPU 单条 100 帧片段（20 个采样点） | ~6.8s，其中约 5s 是 worker 启动 + 模型加载 |
| GPU 推理（加载完成后） | 约 0.05s/帧（batch=8，num_beams=1） |
| CPU 单帧（1280×720，spike 实测） | 约 1.0s/帧 |

模型加载占了单条片段耗时的大头，因为每个分析任务起一个 worker 进程。30 秒视频
（60 个采样点）预计 8–10s；更长的视频需要把 worker 做成常驻进程（见 §5）。

## 4. 这组数字能证明什么

- **零样本可用**：Florence-2 没有针对本项目微调，就能找到 22px 的文字水印。
- **整幅误报被挡住**：无水印素材上模型返回 99.6% 的整幅框，`max_area_ratio=0.25`
  把它过滤掉，结果是 `watermarks: []` + `no_watermark`。这条过滤是正确性前提，不是优化。
- **时序规则成立**：移动水印判 `moving`；出现→消失→再现得到 **1 个水印 2 段区间**；
  跨 3 个镜头保持 1 个水印、3 段区间、3 个待处理片段。
- **坐标换算成立**：1080p 素材上真实模型的框回到源坐标后仍与真值对齐。
- **worker 链路成立**：真实权重、真实 GPU、离线运行时，从采样到建议全部走代码路径。

## 5. 不能证明什么 / 已知限制

- **安装路径**：`model.install` 只由人工跑过一次（记录在 §3/§6），JSON-RPC 入口由
  mock 的单元测试覆盖；没有"RPC 安装 → 分析"的自动化组合测试。
- **真实素材**：夹具是合成画面。压缩噪点、渐变背景、半透明图形 logo、10px 以下小字、
  与背景同色的水印都未覆盖。
- **非文字水印**：只有文字类用例；图形 logo 的召回未知。
- **`min_confidence` 对 Florence 无效**：模型不输出分数，`BOX_CONFIDENCE` 恒为 1.0，
  这个契约参数目前是空转的；唯一的门是面积过滤。
- **静态水印只有一个关键帧**：命中率实际是"与首帧框的 IoU"，缓慢漂移的静态水印
  发现不了（会被判成 static 并用一个矩形覆盖全程）。
- **模型加载成本**：每个分析任务重新加载模型；长视频需要常驻 worker。
- **transformers 版本**：必须 `<5`（5.x 让 Florence-2 的 remote code 崩在
  `forced_bos_token_id`）。运行时钉 4.46.3，实际版本写进 `state.json`；
  torch 由 cu128 索引解析，未钉死小版本，升级前必须重跑本评测。
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
