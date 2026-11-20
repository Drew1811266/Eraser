import type { EraserBridge } from '../shared/ipc';

declare global {
  interface Window {
    eraser: EraserBridge;
  }
}

export {};
