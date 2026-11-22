// 错误码映射：已知码翻成人话并保留细节，未知码原样显示，绝不吞掉原因。
import { describe, expect, it } from 'vitest';
import { analysisError } from './analysisErrors';

const dictionary: Record<string, string> = {
  'error.detectorMissing': '检测器未安装',
  'error.taskNotFound': '找不到该任务',
};

const t = (key: string): string => dictionary[key] ?? key;

describe('analysisError', () => {
  it('translates a known code and keeps the detail', () => {
    expect(analysisError(new Error('DETECTOR_MISSING: no runtime'), t)).toBe(
      '检测器未安装（no runtime）',
    );
  });

  it('translates a known code without detail', () => {
    expect(analysisError(new Error('TASK_NOT_FOUND'), t)).toBe('找不到该任务');
  });

  it('passes an unknown code through untouched', () => {
    expect(analysisError(new Error('SOMETHING_ELSE: keep me'), t)).toBe('SOMETHING_ELSE: keep me');
  });

  it('handles a plain string', () => {
    expect(analysisError('boom', t)).toBe('boom');
  });

  it('falls back to the key when the dictionary lacks it', () => {
    expect(analysisError(new Error('ENGINE_EXITED: gone'), t)).toBe('error.engineExited（gone）');
  });
});
