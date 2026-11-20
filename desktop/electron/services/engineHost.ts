/**
 * Owns the engine process for the app's lifetime.
 *
 * The renderer must never talk to a socket, a pipe or a subprocess directly, so
 * this is the single seam: it starts the engine, forwards its notifications as
 * Electron IPC events, restarts it once if it dies unexpectedly, and gives the
 * IPC handlers a `request` that fails with a readable message instead of a
 * hang when the engine is not there.
 */

import { EventEmitter } from 'node:events';
import type { Capabilities, EngineState, TaskStatus } from '../../shared/ipc';
import { EngineClient, EngineError } from './engineClient';
import { resolveEngineLaunch } from './paths';

const STATE_EVENT = 'state';
const PROGRESS_EVENT = 'progress';
const LOG_EVENT = 'log';

export class EngineHost extends EventEmitter {
  private client: EngineClient | null = null;
  private state: EngineState = { status: 'stopped' };
  private starting: Promise<Capabilities> | null = null;
  private restarts = 0;

  get currentState(): EngineState {
    return this.state;
  }

  async start(): Promise<Capabilities> {
    if (this.client?.isRunning && this.state.status === 'ready') {
      return this.state.capabilities as Capabilities;
    }
    if (this.starting) {
      return this.starting;
    }

    this.setState({ status: 'starting' });
    this.starting = this.doStart().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  private async doStart(): Promise<Capabilities> {
    const launch = resolveEngineLaunch();
    const client = new EngineClient({
      command: launch.command,
      args: launch.args,
      cwd: launch.cwd,
      onStderr: (line) => this.emit(LOG_EVENT, line),
    });
    this.client = client;

    client.on('notification', (notification: { method: string; params: Record<string, unknown> }) => {
      if (notification.params && typeof notification.params === 'object' && 'progress' in notification.params) {
        this.emit(PROGRESS_EVENT, notification.params as unknown as TaskStatus);
      }
    });

    client.on('exit', (info: { code: number | null }) => {
      this.client = null;
      if (this.state.status === 'stopped') {
        return;
      }
      this.emit(LOG_EVENT, `engine exited (code ${info.code ?? 'null'})`);
      this.setState({ status: 'failed', detail: `Engine exited with code ${info.code ?? 'null'}` });
      // One automatic restart: a transient crash should not force the user to
      // relaunch the app, but a crash loop must not spin forever.
      if (this.restarts < 1) {
        this.restarts += 1;
        void this.start().catch((error: Error) => {
          this.emit(LOG_EVENT, `engine restart failed: ${error.message}`);
        });
      }
    });
    client.on('protocol-error', (error: Error) => {
      this.emit(LOG_EVENT, `protocol error: ${error.message}`);
    });

    try {
      const capabilities = await client.start();
      this.restarts = 0;
      this.setState({ status: 'ready', capabilities });
      return capabilities;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setState({ status: 'failed', detail: message });
      throw error;
    }
  }

  async request<M extends string>(method: M, params: Record<string, unknown>): Promise<unknown> {
    if (!this.client?.isRunning) {
      await this.start().catch(() => undefined);
    }
    if (!this.client?.isRunning) {
      throw new EngineError('ENGINE_EXITED', this.state.detail ?? 'Engine is not running');
    }
    return this.client.request(method as never, params as never);
  }

  async stop(): Promise<void> {
    this.setState({ status: 'stopped' });
    const client = this.client;
    this.client = null;
    if (!client) {
      return;
    }
    try {
      await client.shutdown();
    } catch {
      // The engine is going away either way.
    }
  }

  private setState(next: EngineState): void {
    this.state = next;
    this.emit(STATE_EVENT, next);
  }
}

export const ENGINE_EVENTS = { state: STATE_EVENT, progress: PROGRESS_EVENT, log: LOG_EVENT };
