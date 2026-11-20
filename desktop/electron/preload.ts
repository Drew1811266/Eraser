/**
 * The only object the renderer may touch.
 *
 * `contextIsolation` stays on and nothing from Node crosses this boundary: the
 * bridge exposes calls, not objects, and the event subscriptions return their
 * own unsubscribe function so a React effect can clean up without knowing how
 * IPC listeners are keyed.
 */

import { contextBridge, ipcRenderer } from 'electron';
import {
  IPC,
  type Capabilities,
  type EngineState,
  type EraserBridge,
  type PickResult,
  type ProbeResult,
  type StartRequest,
  type TaskStatus,
} from '../shared/ipc';

function subscribe<T>(channel: string, listener: (payload: T) => void): () => void {
  const wrapped = (_event: Electron.IpcRendererEvent, payload: T): void => listener(payload);
  ipcRenderer.on(channel, wrapped);
  return () => {
    ipcRenderer.removeListener(channel, wrapped);
  };
}

const bridge: EraserBridge = {
  capabilities: (): Promise<Capabilities> => ipcRenderer.invoke(IPC.capabilities),
  pickVideo: (): Promise<PickResult> => ipcRenderer.invoke(IPC.pickVideo),
  pickOutputDir: (): Promise<PickResult> => ipcRenderer.invoke(IPC.pickOutputDir),
  probe: (path: string): Promise<ProbeResult> => ipcRenderer.invoke(IPC.probe, path),
  start: (request: StartRequest): Promise<{ task_id: string }> => ipcRenderer.invoke(IPC.start, request),
  cancel: (taskId: string): Promise<{ ok: boolean }> => ipcRenderer.invoke(IPC.cancel, taskId),
  status: (taskId: string): Promise<TaskStatus> => ipcRenderer.invoke(IPC.status, taskId),
  reveal: (path: string): Promise<void> => ipcRenderer.invoke(IPC.reveal, path),
  openExternal: (path: string): Promise<void> => ipcRenderer.invoke(IPC.openExternal, path),
  defaultOutputDir: (): Promise<string> => ipcRenderer.invoke(IPC.defaultOutputDir),
  onProgress: (listener) => subscribe<TaskStatus>(IPC.progress, listener),
  onEngineState: (listener) => subscribe<EngineState>(IPC.engineState, listener),
  onEngineLog: (listener) => subscribe<string>(IPC.engineLog, listener),
};

contextBridge.exposeInMainWorld('eraser', bridge);
