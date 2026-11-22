// 引擎错误 → 用户可读文案。错误码由主进程桥以 "CODE: message" 形式抛出。
const KNOWN: Record<string, string> = {
  DETECTOR_MISSING: 'error.detectorMissing',
  TASK_NOT_FOUND: 'error.taskNotFound',
  INVALID_PARAMS: 'error.invalidParams',
  ENGINE_EXITED: 'error.engineExited',
  NOT_IMPLEMENTED: 'error.notImplemented',
};

export function analysisError(error: unknown, t: (key: string) => string): string {
  const raw = error instanceof Error ? error.message : String(error);
  const colon = raw.indexOf(':');
  // 没有冒号时整串就是错误码（引擎有时只发码不发消息）。
  const code = (colon > 0 ? raw.slice(0, colon) : raw).trim();
  const detail = colon > 0 ? raw.slice(colon + 1).trim() : '';
  const key = KNOWN[code];
  if (!key) {
    return raw;
  }
  const translated = t(key);
  return detail ? `${translated}（${detail}）` : translated;
}
