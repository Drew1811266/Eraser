# ADR-0002 · 引擎契约：stdio 上的 JSON-RPC 2.0

- **状态**：**已采纳**（2026-11-20）
- **日期**：2026-11-19
- **关联**：ADR-0001

---

## 背景

外壳（Electron/TS）与引擎（Python）必须通信。候选：stdio JSON-RPC、localhost HTTP、原生 FFI（napi/node-addon）、命名管道。

参考仓库当前的对照物是：`src/gui/api.py` 的 69 个 pywebview `js_api` 方法，与 `frontend/electron/ipcHandlers.ts` 的 32 个 IPC 通道。二者**语义重叠但结构不一致**，这正是需要一个唯一契约源的原因。

---

## 决策

**采用 JSON-RPC 2.0 over stdio（新行分隔 UTF-8）**，配合 `contract/methods.json` 作为唯一事实源。

### 消息形态

```jsonc
// 请求
{"jsonrpc":"2.0","id":"7","method":"process.start","params":{...}}
// 响应
{"jsonrpc":"2.0","id":"7","result":{"task_id":"..."}}
// 事件（无 id）
{"jsonrpc":"2.0","method":"progress","params":{"task_id":"...","phase":"infer",...}}
// 错误
{"jsonrpc":"2.0","id":"7","error":{"code":-32001,"message":"...","data":{"code":"ENGINE_MODEL_MISSING"}}}
```

### 关键规则
1. **stdout 只承载协议帧**；引擎所有调试输出强制走 stderr，否则会污染协议（这是最容易踩的坑）。
2. 每个请求必须有 `id`，长任务立即返回 `task_id`，进度通过 `progress` 事件推送。
3. 引擎启动后先发一条 `ready` 事件，附带 `{version, capabilities, platform}`；外壳据此决定 UI 可用性，而不是先猜。
4. 未知方法返回 `-32601`，参数不符返回 `-32602`，**不得抛裸异常**。

---

## 备选方案与取舍

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **stdio JSON-RPC** | 无端口、**不触发 Windows 防火墙弹窗**、生命周期跟随父进程、无需鉴权 | 需自行实现三路复用；大二进制无法直接传 | **选定** |
| localhost HTTP | 生态成熟、易调试、可传大文件 | 首启必弹防火墙授权；端口冲突；需本地鉴权；进程孤儿风险 | 否 |
| 命名管道 | Windows 原生、性能好 | 跨平台不统一；Electron 侧 Node API 支持一般 | 否 |
| 原生 FFI | 零序列化开销 | torch 崩溃会拖垮整个应用；GIL 与线程模型复杂；无进程隔离 | 否（作为未来优化项） |

> **防火墙这一条是决定性的**：Windows 上任何 `bind()` 监听都会触发安全中心弹窗。对一款视频工具来说，用户第一次启动就被问"是否允许该应用通过网络通信"，是明确的产品伤害。

---

## 大数据的传输

不通过 RPC 传帧或视频。原则：**RPC 只传路径与元数据，媒体数据走文件系统。**

- 帧预览：引擎写临时 JPEG，外壳通过 `file://` 或帧服务读取；
- 标注：外壳读写 sidecar，引擎只接受路径；
- 模型：引擎自行校验与安装，外壳只展示进度。

---

## 后果

- 需要实现并测试一层 RPC server（约 200 行 Python + 150 行 TS），以及一套"双端共享的测试向量"。
- 调试体验依赖日志：必须提供 `--rpc-trace` 开关，把协议帧写入单独的 trace 文件。
- 为未来替换引擎留出空间：任何语言实现的引擎，只要满足契约即可上线。
