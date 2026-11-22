# M3 · 分析界面实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 M1/M2 的分析能力变成用户能看、能改的界面——分析报告、时间线、水印编辑栏，确认后才开始处理。

**Architecture:** 新增一个 `analyze` 主视图，不改动已有的打标/结果/放大视图。分析结果进入一个新的 zustand slice（`tracks` 是可编辑副本），时间线的所有编辑都是**纯函数**（`store/analysisOps.ts`），只有点"开始处理"时才调用一次 `annotation.compile`，把轨道摊平成 `Segment[]` 交给现有 `process.start`。

**Tech Stack:** React 19 + zustand + vitest + @testing-library/react（已在 devDependencies 里）+ 契约生成的 TS 类型（`desktop/electron/generated/contract.ts` 已导出 `Analysis`/`WatermarkTrack`/`Segment`/`DetectorEntry`）。

**Spec:** [docs/09-model-and-automation-redesign.md](09-model-and-automation-redesign.md) §8（界面）、§5.5（编译）、§7.3（错误码）

---

## 0. 已核实的现状（不要重新猜）

- 视图：`MainView = 'process' | 'annotate' | 'result' | 'upscale'`（`desktop/renderer/types/app.ts:2`），由 `App.tsx` 的 `renderView()` 渲染。
- 桥：渲染层通过 `window.eraser`（`EraserBridge`，`desktop/shared/ipc.ts`）调用特定通道；主进程 handler 在 `desktop/electron/ipc.ts` 里统一 `host.request('engine.method', params)`。**不要**走 `window.wmr` 那套遗留 snake_case 路由。
- 引擎已具备：`analyze.capabilities`、`analyze.start`、`process.status`、`annotation.compile`、`model.install`、`model.installStatus`。
- 预览：`App.tsx` 已维护 `frameImageUrl` / `previewSessionId`，`AnnotationWorkspace` 用它画框；时间线拖动帧号时复用它。
- i18n：`messages.ts` 中 `zh` 是完整词典，`en` 缺 key 时回退中文；`t(key)` 找不到 key 会原样返回 key。
- 测试：`desktop` 里 `npm test` = vitest，已有 25 条（主进程）；渲染层组件测试可直接用 jsdom + testing-library。

## Global Constraints

- **不改变已有视图的行为**：`annotate`/`result`/`upscale` 的现有测试与交互必须原样通过。
- 所有引擎调用都经过 `window.eraser`，渲染进程不直接 `fetch`/`ipcRenderer`。
- 分析结果的类型一律来自生成的契约文件，不手写第二份。
- 时间线的编辑逻辑必须是纯函数（无 React、无 store），可单独单测。
- 只有点"开始处理"才编译一次；编辑过程中**不**调用 RPC。
- 中文文案进 `messages.ts`，`en` 缺省可回退。

## Review Focus

- **没有检测器**：分析视图必须能引导安装（`model.install`），安装中显示进度，失败显示原因并能重试。
- **分析失败/取消**：`process.status` 返回 failed/cancelled 时，界面要显示原因，不能停在"分析中"。
- **无水印**：`watermarks: []` 时显示"没有检测到水印"，并允许手动添加一个框。
- **越界编辑**：把区间拖过 0 或 `frame_count-1`、把两个区间拖到重叠、删除最后一个区间——都不能产生非法 `Segment`。
- **重复点击**："开始处理"要被禁用/去重，不能起两个任务。
- **切点**：时间线要显示镜头切点，用户能看出"水印跨了几个镜头"。

---

### Task 1: 桥与 IPC

**Files:**
- Modify: `desktop/shared/ipc.ts`（4 个通道 + `EraserBridge` 方法）
- Modify: `desktop/electron/ipc.ts`（4 个 handler）
- Modify: `desktop/electron/preload.ts`（暴露方法）
- Test: `desktop/electron/preload.test.ts`、`desktop/electron/ipc.test.ts`

**Interfaces（新增）：**

```ts
// shared/ipc.ts
analyzeCapabilities(): Promise<{ detectors: DetectorEntry[]; available: boolean }>
analyzeStart(request: { input_path: string; sample_step?: number }): Promise<{ task_id: string }>
compileAnnotations(request: {
  watermarks: WatermarkTrack[];
  shots: FrameInterval[];
  video: { width: number; height: number; frame_count: number };
}): Promise<{ segments: Segment[] }>
installDetector(request: { model_id: string }): Promise<{ task_id: string }>
```

- [ ] **Step 1: 写失败测试**（preload 暴露了四个方法；ipc handler 把它们转发到正确的引擎方法）

```ts
it('exposes the analysis bridge', () => {
  // 断言 preload 构造的 bridge 里存在四个方法，且调用时 invoke 到对应通道
  expect(bridge.analyzeCapabilities).toBeTypeOf('function');
  expect(bridge.analyzeStart).toBeTypeOf('function');
  expect(bridge.compileAnnotations).toBeTypeOf('function');
  expect(bridge.installDetector).toBeTypeOf('function');
});

it('forwards analysis calls to the engine methods', async () => {
  // 用假的 host.request 断言：analyze.start / annotation.compile / model.install / analyze.capabilities
});
```

- [ ] **Step 2: 跑 `cd desktop && npm test` 确认失败**
- [ ] **Step 3: 实现** 四个通道、`EraserBridge` 方法、`ipcMain.handle` 转发、preload 绑定
- [ ] **Step 4: `npm test` 全绿 + `npm run typecheck`**
- [ ] **Step 5: 提交** `feat(shell): expose the analysis surface to the renderer`

---

### Task 2: 编辑模型（纯函数）与 store

**Files:**
- Create: `desktop/renderer/store/analysisOps.ts`
- Create: `desktop/renderer/store/analysis.ts`
- Test: `desktop/renderer/store/analysisOps.test.ts`

**Interfaces：**

```ts
// analysisOps.ts —— 全部纯函数，输入输出都是 WatermarkTrack / Interval
type Edge = 'start' | 'end';
nudgeInterval(interval: FrameInterval, edge: Edge, delta: number, frameCount: number): FrameInterval
splitTrack(track: WatermarkTrack, intervalIndex: number, frame: number): WatermarkTrack
mergeWithNext(track: WatermarkTrack, intervalIndex: number): WatermarkTrack
deleteInterval(track: WatermarkTrack, intervalIndex: number): WatermarkTrack | null
addManualTrack(tracks, rect, frame, frameCount): WatermarkTrack[]
clampIntervals(tracks: WatermarkTrack[], frameCount: number): WatermarkTrack[]
```

- [ ] **Step 1: 写失败测试**（越界、重叠、删除最后一段、切分点落在区间外）
- [ ] **Step 2: 确认失败** `cd desktop && npx vitest run renderer/store/analysisOps.test.ts`
- [ ] **Step 3: 实现纯函数**（区间半开？不——沿用闭区间 `[start, end]`，与 `Segment` 一致）
- [ ] **Step 4: 实现 zustand slice**（`analysis`、`tracks`、`selection`、`currentFrame`、`dirty`，以及 `setAnalysis/select/nudge/split/merge/delete/add/reset`）
- [ ] **Step 5: 提交** `feat(ui): editable watermark tracks as pure operations`

---

### Task 3: 分析报告卡片

**Files:**
- Create: `desktop/renderer/views/AnalysisReport.tsx`
- Test: `desktop/renderer/views/AnalysisReport.test.tsx`

**内容：** 水印数、镜头数、每个 track 的 motion 与区间数、`suggested.warnings` 的人话映射（`no_watermark` / `moving_watermark` / `multiple_watermarks` / `low_confidence` / `quality_engine_unavailable`）、检测器状态（可用 / 需安装 / 安装进度 / 安装失败原因）。

- [ ] **Step 1: 写失败测试**（有结果 / 无结果 / 检测器缺失 / 安装失败 四种渲染）
- [ ] **Step 2: 确认失败** → [ ] **Step 3: 实现** → [ ] **Step 4: 绿** → [ ] **Step 5: 提交**

---

### Task 4: 时间线

**Files:**
- Create: `desktop/renderer/components/Timeline.tsx`
- Test: `desktop/renderer/components/Timeline.test.tsx`

**结构：** 顶部时间刻度；每条 track 一行泳道；区间是块（左/右各有拖拽手柄 + 按钮微调 1 帧）；镜头切点是竖直虚线；播放头显示 `currentFrame`；点击块选中、点击空白处 seek。

**Interfaces：**

```ts
interface TimelineProps {
  tracks: WatermarkTrack[];
  shots: FrameInterval[];
  frameCount: number;
  fps: number;
  currentFrame: number;
  selected: { trackId: string; intervalIndex: number } | null;
  onSelect(trackId: string, intervalIndex: number): void;
  onSeek(frame: number): void;
  onNudge(trackId: string, intervalIndex: number, edge: Edge, delta: number): void;
}
```

- [ ] **Step 1: 写失败测试**（渲染块数量与位置、点击选中、点手柄 nudge、切点数量）
- [ ] **Step 2: 确认失败** → [ ] **Step 3: 实现**（宽度按 `frameCount` 归一化；最小块宽 8px，避免 1 帧块看不见）
- [ ] **Step 4: 绿** → [ ] **Step 5: 提交**

---

### Task 5: 分析视图

**Files:**
- Create: `desktop/renderer/views/AnalysisView.tsx`
- Modify: `desktop/renderer/types/app.ts`（`MainView` 加 `'analyze'`）
- Test: `desktop/renderer/views/AnalysisView.test.tsx`

**行为：**
1. 进入即调 `analyzeCapabilities`；没有可用检测器 → 显示安装卡片（`installDetector` + 轮询 `status`）。
2. `analyzeStart` → 轮询 `status`（复用 `App` 里的 `sleep` 模式，间隔 250ms）→ 成功写入 store，失败显示 `error`。
3. 报告 + 预览 + 时间线 + 工具栏（拆分 / 合并 / 删除 / 手动添加 / 重新分析）。
4. "开始处理"：`compileAnnotations` → `onStart(segments)`（由 App 调 `process.start` 并切到 result 视图）；按钮在编译中/处理中禁用。

- [ ] **Step 1: 写失败测试**（无检测器→安装卡；分析成功→显示报告；失败→显示原因；开始处理→编译一次且按钮禁用）
- [ ] **Step 2: 确认失败** → [ ] **Step 3: 实现** → [ ] **Step 4: 绿** → [ ] **Step 5: 提交**

---

### Task 6: 接入 App 与预览

**Files:**
- Modify: `desktop/renderer/App.tsx`（`renderView` 加 `analyze`；ProcessView 增加入口按钮；开始处理回调）
- Modify: `desktop/renderer/views/ProcessView.tsx`（"分析水印"按钮）
- Test: `desktop/renderer/App.test.tsx`（新增，验证视图切换与开始处理的调用参数）

- [ ] **Step 1: 写失败测试**（点"分析水印"切到 analyze；`onStart` 用编译出的 segments 调 `process.start`）
- [ ] **Step 2: 确认失败** → [ ] **Step 3: 实现** → [ ] **Step 4: 绿**
- [ ] **Step 5: 提交** `feat(ui): analysis view wired into the app flow`

---

### Task 7: 文案与错误

- [ ] `messages.ts` 增加 `analyze.*` 键（zh 完整、en 同步）
- [ ] 引擎错误码映射：`DETECTOR_MISSING` → 去安装；`TASK_NOT_FOUND` → 重新分析；其余显示 `message`
- [ ] 提交

---

### Task 8: 收口

- [ ] `bash scripts/check.sh` 全绿（含新增 UI 测试）
- [ ] `docs/09` §11：W7/W8/W9 状态更新
- [ ] `CHANGELOG.md` M3 小节
- [ ] 整分支审查

---

## 风险

| # | 风险 | 处置 |
|---|---|---|
| R1 | `App.tsx` 太大，改动可能碰坏已有流程 | 新视图自包含，App 只加一个 `case` 和一个回调；现有 25 条测试是回归网 |
| R2 | 时间线拖拽在 jsdom 里难以测试 | 拖拽与"按钮微调"共用同一个 `onNudge`；测试走按钮路径 |
| R3 | 分析任务轮询在视图卸载后继续 | 在 effect cleanup 里置 `cancelled` 标志，与 `App` 现有轮询一致 |
| R4 | 手动添加框与已有拖框交互重复 | 复用 `AnnotationWorkspace` 的拖框坐标换算，不重写 |
| R5 | 1080p 素材的时间线性能 | 时间线只渲染区间块与切点，不逐帧渲染 |
