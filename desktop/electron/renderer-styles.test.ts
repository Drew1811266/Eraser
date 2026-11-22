// @vitest-environment node
/**
 * The renderer's class names must actually have rules.
 *
 * M3 shipped a timeline whose class names existed only in JSX: every block was a
 * static div at full width, the handles were invisible and the playhead never
 * showed. Nothing failed, because no test looked at the stylesheet.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const CSS = readFileSync(join(__dirname, '..', 'renderer', 'styles.css'), 'utf8');

describe('analysis view styles', () => {
  it('styles every class the timeline renders', () => {
    for (const name of [
      'timeline__block',
      'timeline__lane',
      'timeline__cut',
      'timeline__playhead',
      'timeline__handle',
      'timeline__overlay',
      'timeline__nudges',
      'timeline__scale',
    ]) {
      expect(CSS.includes(`.${name}`), `${name} has no CSS`).toBe(true);
    }
  });

  it('keeps a one-frame block clickable', () => {
    expect(CSS).toMatch(/\.timeline__block\s*{[^}]*min-width/s);
  });

  it('styles the analysis view and the report', () => {
    for (const name of ['analysis-view', 'analysis-report', 'analysis-stage__box']) {
      expect(CSS.includes(`.${name}`), `${name} has no CSS`).toBe(true);
    }
  });
});
