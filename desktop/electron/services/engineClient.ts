/**
 * EngineClient: the shell's only door to the Python engine.
 *
 * Transport is newline-delimited JSON-RPC 2.0 over the child's stdio
 * (docs/adr/0002-engine-contract.md). Two invariants this class exists to hold:
 *
 *   1. stdout is protocol-only. Anything unparseable is surfaced as a
 *      'protocol-error' event, never silently dropped.
 *   2. Every in-flight request is settled exactly once — by result, by error,
 *      by timeout, or by process death. A hung promise is a hung UI.
 *
 * Deliberately dependency-free (node builtins only) so it can be exercised
 * directly under `node --experimental-strip-types` without a build step.
 */

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { createInterface, type Interface } from 'node:readline';

import type {
  Capabilities,
  ErrorCode,
  MethodName,
  MethodParams,
  MethodResults,
} from '../generated/contract.js';

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const DEFAULT_READY_TIMEOUT_MS = 20_000;
const DEFAULT_SHUTDOWN_TIMEOUT_MS = 5_000;

export interface EngineClientOptions {
  /** Executable: `python`, an absolute python path, or the frozen engine exe. */
  command: string;
  /** Defaults to `['-m', 'engine.main']`; ignored for a frozen executable. */
  args?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  requestTimeoutMs?: number;
  readyTimeoutMs?: number;
  /** Receives engine logs. The engine must never write logs to stdout. */
  onStderr?: (line: string) => void;
}

export interface EngineNotification {
  method: string;
  params: Record<string, unknown>;
}

/** An error returned by the engine, carrying the stable application code. */
export class EngineError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly detail: Record<string, unknown>;

  constructor(
    code: string,
    message: string,
    detail: Record<string, unknown> = {},
    retryable = false,
  ) {
    super(message);
    this.name = 'EngineError';
    this.code = code;
    this.retryable = retryable;
    this.detail = detail;
  }
}

interface PendingRequest {
  method: string;
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
}

export class EngineClient extends EventEmitter {
  private readonly options: EngineClientOptions;
  private child: ChildProcessWithoutNullStreams | null = null;
  private reader: Interface | null = null;
  private readonly pending = new Map<number, PendingRequest>();
  private nextId = 1;
  private capabilities: Capabilities | null = null;
  private readyResolve: ((value: Capabilities) => void) | null = null;
  private readyReject: ((reason: Error) => void) | null = null;
  private readyTimer: NodeJS.Timeout | null = null;
  private shuttingDown = false;
  private exited = false;

  constructor(options: EngineClientOptions) {
    super();
    this.options = options;
  }

  get isRunning(): boolean {
    return this.child !== null && !this.exited;
  }

  get pendingCount(): number {
    return this.pending.size;
  }

  get engineCapabilities(): Capabilities | null {
    return this.capabilities;
  }

  /**
   * Spawn the engine and wait for its `ready` frame.
   *
   * Resolves only once the engine has proven it can speak the protocol, so a
   * broken interpreter or a missing module fails here instead of producing a
   * mystery timeout on the first real call.
   */
  async start(): Promise<Capabilities> {
    if (this.child) {
      throw new EngineError('ALREADY_STARTED', 'Engine is already running');
    }

    const args = this.options.args ?? ['-m', 'engine.main'];
    const child = spawn(this.options.command, args, {
      cwd: this.options.cwd,
      env: { ...process.env, ...this.options.env },
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    }) as ChildProcessWithoutNullStreams;

    this.child = child;
    this.exited = false;

    if (this.options.onStderr) {
      const stderrReader = createInterface({ input: child.stderr });
      stderrReader.on('line', (line) => this.options.onStderr?.(line));
    } else {
      child.stderr.resume();
    }

    this.reader = createInterface({ input: child.stdout });
    this.reader.on('line', (line) => this.handleLine(line));

    child.on('error', (error) => {
      this.failAll(new EngineError('ENGINE_SPAWN_FAILED', error.message));
      this.rejectReady(new EngineError('ENGINE_SPAWN_FAILED', error.message));
    });

    child.on('exit', (code, signal) => {
      this.exited = true;
      const detail = `code=${code ?? 'null'} signal=${signal ?? 'null'}`;
      this.failAll(new EngineError('ENGINE_EXITED', `Engine exited (${detail})`, { code, signal }));
      this.rejectReady(new EngineError('ENGINE_EXITED', `Engine exited before ready (${detail})`));
      this.emit('exit', { code, signal });
    });

    return await this.waitForReady();
  }

  /** Send a request and resolve with its result. */
  async request<M extends MethodName>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    return (await this.send(method, params as Record<string, unknown>)) as MethodResults[M];
  }

  /** Send a request without waiting for its result (fire and forget). */
  notify(method: string, params: Record<string, unknown> = {}): void {
    this.writeFrame({ jsonrpc: '2.0', method, params });
  }

  /**
   * Ask the engine to exit, then make sure it really did.
   *
   * Escalates to kill on timeout: a stuck engine must not keep the app alive.
   */
  async shutdown(timeoutMs = DEFAULT_SHUTDOWN_TIMEOUT_MS): Promise<void> {
    if (!this.child || this.exited) return;
    this.shuttingDown = true;

    const exited = new Promise<void>((resolve) => {
      this.child?.once('exit', () => resolve());
    });

    try {
      await this.send('system.shutdown', {}, timeoutMs);
    } catch {
      // The engine may exit before replying; only the exit event matters.
    }

    const timedOut = await Promise.race([
      exited.then(() => false),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(true), timeoutMs)),
    ]);

    if (timedOut) {
      this.child.kill();
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 1000))]);
    }

    this.dispose();
  }

  /** Force-release resources. Safe to call repeatedly. */
  dispose(): void {
    this.reader?.close();
    this.reader = null;
    if (this.readyTimer) {
      clearTimeout(this.readyTimer);
      this.readyTimer = null;
    }
    this.failAll(new EngineError('ENGINE_DISPOSED', 'Engine client was disposed'));
    if (this.child && !this.exited) {
      this.child.kill();
    }
    this.child = null;
    this.removeAllListeners();
  }

  // -- internals -----------------------------------------------------------

  private send(
    method: string,
    params: Record<string, unknown>,
    timeoutMs = this.options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<unknown> {
    return new Promise((resolve, reject) => {
      if (!this.child || this.exited) {
        reject(new EngineError('ENGINE_NOT_RUNNING', 'Engine is not running'));
        return;
      }

      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new EngineError('TIMEOUT', `Request '${method}' timed out after ${timeoutMs}ms`, {
            method,
            timeout_ms: timeoutMs,
          }),
        );
      }, timeoutMs);

      this.pending.set(id, { method, resolve, reject, timer });
      this.writeFrame({ jsonrpc: '2.0', id, method, params });
    });
  }

  private writeFrame(frame: Record<string, unknown>): void {
    const stdin = this.child?.stdin;
    if (!stdin || stdin.destroyed) {
      throw new EngineError('ENGINE_NOT_RUNNING', 'Engine stdin is not writable');
    }
    stdin.write(`${JSON.stringify(frame)}\n`);
  }

  private handleLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;

    let frame: Record<string, unknown>;
    try {
      frame = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      this.emit('protocol-error', { line: trimmed, reason: 'not json' });
      return;
    }

    const hasId = Object.prototype.hasOwnProperty.call(frame, 'id');
    if (hasId) {
      this.settle(frame);
      return;
    }

    const method = typeof frame.method === 'string' ? frame.method : '';
    const params = (frame.params ?? {}) as Record<string, unknown>;

    if (method === 'ready') {
      this.capabilities = params as unknown as Capabilities;
      this.resolveReady(this.capabilities);
      return;
    }

    if (!method) {
      this.emit('protocol-error', { line: trimmed, reason: 'frame has neither id nor method' });
      return;
    }

    this.emit('notification', { method, params } satisfies EngineNotification);
  }

  private settle(frame: Record<string, unknown>): void {
    const id = frame.id as number;
    const pending = this.pending.get(id);
    if (!pending) {
      // A response to a request we already gave up on (timeout). Not fatal.
      this.emit('orphan-response', { id, method: frame.method });
      return;
    }

    clearTimeout(pending.timer);
    this.pending.delete(id);

    if (frame.error) {
      const error = frame.error as {
        message?: string;
        data?: { code?: string; retryable?: boolean; detail?: Record<string, unknown> };
      };
      pending.reject(
        new EngineError(
          error.data?.code ?? 'INTERNAL_ERROR',
          error.message ?? 'Engine error',
          error.data?.detail ?? {},
          Boolean(error.data?.retryable),
        ),
      );
      return;
    }

    pending.resolve(frame.result);
  }

  private failAll(error: Error): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      this.pending.delete(id);
      pending.reject(error);
    }
  }

  private waitForReady(): Promise<Capabilities> {
    if (this.capabilities) return Promise.resolve(this.capabilities);

    return new Promise<Capabilities>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;

      const timeoutMs = this.options.readyTimeoutMs ?? DEFAULT_READY_TIMEOUT_MS;
      this.readyTimer = setTimeout(() => {
        this.readyTimer = null;
        reject(
          new EngineError(
            'READY_TIMEOUT',
            `Engine did not report ready within ${timeoutMs}ms`,
            { timeout_ms: timeoutMs },
          ),
        );
      }, timeoutMs);
    });
  }

  private resolveReady(capabilities: Capabilities): void {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer);
      this.readyTimer = null;
    }
    this.readyResolve?.(capabilities);
    this.readyResolve = null;
    this.readyReject = null;
  }

  private rejectReady(error: Error): void {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer);
      this.readyTimer = null;
    }
    this.readyReject?.(error);
    this.readyResolve = null;
    this.readyReject = null;
  }
}

export type { ErrorCode };
