// @vitest-environment node
/**
 * Every `t('...')` key in the renderer must exist in both dictionaries.
 *
 * The analysis view once rendered the raw key `analyze.intervals` on screen: the
 * fallback (return the key) is right for debugging and invisible to every test
 * that asserts behaviour instead of text. A real browser screenshot caught it;
 * this test keeps it caught.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const RENDERER = join(__dirname, '..', 'renderer');
const MESSAGES = readFileSync(join(RENDERER, 'i18n', 'messages.ts'), 'utf8');
const zh = MESSAGES.slice(0, MESSAGES.indexOf('const en:'));
const en = MESSAGES.slice(MESSAGES.indexOf('const en:'));

function sources(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !entry.name.endsWith('.tsx')) {
      continue;
    }
    if (entry.name.endsWith('.test.tsx')) {
      continue;
    }
    found.push(join(entry.parentPath, entry.name));
  }
  return found;
}

const used = new Set<string>();
for (const file of sources(RENDERER)) {
  const text = readFileSync(file, 'utf8');
  for (const match of text.matchAll(/t\('([A-Za-z]+\.[A-Za-z.]+)'\)/g)) {
    used.add(match[1]!);
  }
}

describe('i18n coverage', () => {
  it('finds the keys the renderer actually uses', () => {
    expect(used.size).toBeGreaterThan(100);
  });

  it('has a Chinese string for every key', () => {
    const missing = [...used].filter((key) => !zh.includes(`'${key}'`));
    expect(missing, `missing in zh: ${missing.join(', ')}`).toEqual([]);
  });

  it('has an English string for every key', () => {
    const missing = [...used].filter((key) => !en.includes(`'${key}'`));
    expect(missing, `missing in en: ${missing.join(', ')}`).toEqual([]);
  });
});
