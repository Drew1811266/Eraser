#!/usr/bin/env node
/**
 * End-to-end contract check: real engine process, real EngineClient, real stdio.
 *
 * This is the M1 acceptance gate (docs/03-migration-plan.md, gate G1):
 *   - the shell can start the engine and receive `ready`
 *   - a request round-trips with its id preserved
 *   - contract violations come back as structured errors, not crashes
 *   - engine logging on stderr cannot corrupt the protocol stream
 *   - shutdown leaves no orphan process
 *
 * Runs under plain Node using native TypeScript stripping, so no build step and
 * no node_modules are required.
 *
 *   node scripts/e2e-engine.mjs
 */

import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * The engine needs the project venv: the system interpreter has neither
 * onnxruntime nor opencv, and the video-backed methods import both.
 */
function resolvePython() {
  if (process.env.ERASER_PYTHON) return process.env.ERASER_PYTHON;
  const venv = path.join(here, '..', '.venv', 'Scripts', 'python.exe');
  return fs.existsSync(venv) ? venv : 'python';
}

const PYTHON = resolvePython();
const repoRoot = path.resolve(here, '..');

const { EngineClient, EngineError } = await import(
  pathToFileURL(path.join(repoRoot, 'desktop', 'electron', 'services', 'engineClient.ts')).href
);

const results = [];
const liveClients = new Set();
let failures = 0;

function check(name, condition, detail = '') {
  const ok = Boolean(condition);
  results.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok || !detail ? '' : ` — ${detail}`}`);
}

function section(title) {
  console.log(`\n${title}`);
}

function newClient(extraEnv = {}, onStderr) {
  const client = new EngineClient({
    command: PYTHON,
    args: ['-m', 'engine.main'],
    cwd: repoRoot,
    requestTimeoutMs: 10_000,
    readyTimeoutMs: 15_000,
    env: extraEnv,
    onStderr,
  });
  liveClients.add(client);
  return client;
}

/** Never leave a child process behind, even when an assertion throws. */
function disposeAll() {
  for (const client of liveClients) {
    try {
      client.dispose();
    } catch {
      // Already gone; nothing to do.
    }
  }
  liveClients.clear();
}

async function main() {
  console.log('Eraser engine end-to-end contract check');

  // ---------------------------------------------------------------- happy path
  section('1. startup and readiness');
  const client = newClient();
  const capabilities = await client.start();

  check('start() resolves with capabilities', Boolean(capabilities));
  check('capabilities carries an engine version', typeof capabilities.engine_version === 'string');
  check(
    'platform key looks like <os>-<arch>',
    /^[a-z0-9]+-[a-z0-9_]+$/.test(capabilities.platform),
    capabilities.platform,
  );
  check(
    'M1 methods are advertised',
    ['system.ping', 'annotation.normalize'].every((m) => capabilities.methods.includes(m)),
    JSON.stringify(capabilities.methods),
  );
  check('a cpu device is always reported', capabilities.devices.some((d) => d.kind === 'cpu'));

  section('2. request round-trip');
  const ping = await client.request('system.ping', {});
  check('ping returns pong', ping.pong === true);
  check('uptime is a number', typeof ping.uptime_ms === 'number');
  check('engine version matches capabilities', ping.engine_version === capabilities.engine_version);

  const [a, b] = await Promise.all([
    client.request('system.ping', {}),
    client.request('system.ping', {}),
  ]);
  check('concurrent requests both settle', a.pong === true && b.pong === true);

  section('3. annotation.normalize parity with the reference rules');
  const normalized = await client.request('annotation.normalize', {
    segments: [
      { start_frame: -5, end_frame: 9999, rect: { x: 1900, y: 1060, width: 300, height: 200 } },
      { start_frame: 10, end_frame: 5, rect: { x: -20, y: -20, width: 0, height: 0 } },
    ],
    video: { width: 1920, height: 1080, frame_count: 900 },
  });

  const segments = normalized.segments;
  check('both segments survived', segments.length === 2, `got ${segments.length}`);
  check(
    'frame range clamped to [0, 899]',
    segments[0].start_frame === 0 && segments[0].end_frame === 899,
    JSON.stringify([segments[0].start_frame, segments[0].end_frame]),
  );
  check(
    'edge rect truncated, not scaled',
    segments[0].rect.width === 20 && segments[0].rect.height === 20,
    JSON.stringify(segments[0].rect),
  );
  check(
    'inverted range swapped',
    segments[1].start_frame === 5 && segments[1].end_frame === 10,
    JSON.stringify([segments[1].start_frame, segments[1].end_frame]),
  );
  check(
    'negative origin clamped, zero size floored to 1',
    segments[1].rect.x === 0 && segments[1].rect.y === 0 &&
      segments[1].rect.width === 1 && segments[1].rect.height === 1,
    JSON.stringify(segments[1].rect),
  );
  check(
    'defaults applied (expand 5, feather 3, enabled)',
    segments[0].expand_px === 5 && segments[0].feather_px === 3 && segments[0].enabled === true,
  );
  check('ids generated', segments.every((s) => typeof s.id === 'string' && s.id.length === 32));


  section('5. preview session, annotations and the model repair path');
  // Built on demand so this script does not depend on which gate ran first.
  const fixtureForPreview = path.join(here, '..', '.cache', 'protocol-fixture.mp4');
  if (!fs.existsSync(fixtureForPreview)) {
    const built = spawnSync(PYTHON, [
      path.join(here, 'e2e-video.py'), '--fixture-only', '--fixture', fixtureForPreview,
    ], { cwd: path.join(here, '..'), encoding: 'utf-8' });
    if (built.status !== 0) {
      console.error(built.stderr?.slice(-400) ?? 'fixture build failed');
    }
  }
  if (fs.existsSync(fixtureForPreview)) try {
    const session = await client.request('preview.open', { path: fixtureForPreview, max_width: 320 });
    check('preview session opens', typeof session.session_id === 'string' && session.frame_count > 0,
      `${session.frame_count} frames`);

    const first = await client.request('preview.frame', { session_id: session.session_id, frame_index: 0 });
    check('a frame comes back as a data URL', String(first.data_url).startsWith('data:image/jpeg;base64,'));
    check('preview frame is scaled down', first.width <= 320, String(first.width));

    const again = await client.request('preview.frame', { session_id: session.session_id, frame_index: 0 });
    check('reading the same frame twice is stable', again.data_url === first.data_url);

    await client.request('preview.close', { session_id: session.session_id });
    const afterClose = await client.request('preview.frame', { session_id: session.session_id, frame_index: 0 })
      .then(() => false)
      .catch((error) => error.code === 'TASK_NOT_FOUND');
    check('closing a session invalidates it', afterClose);

    // Annotations belong next to a real video, which is also what makes the
    // clamping honest: the geometry is known.
    const saved = await client.request('annotation.save', {
      video_path: fixtureForPreview,
      segments: [{ start_frame: 0, end_frame: 10, rect: { x: 1, y: 2, width: 30, height: 40 } }],
    });
    check('annotations are written next to the video', fs.existsSync(saved.sidecar_path), saved.sidecar_path);

    const loaded = await client.request('annotation.load', { video_path: fixtureForPreview });
    check('they read back', loaded.exists === true && loaded.segments.length === 1);
    check('the stored rectangle survives', loaded.segments[0].rect.width === 30);

    const removed = await client.request('annotation.delete', { video_path: fixtureForPreview });
    check('they can be deleted', removed.ok === true && !fs.existsSync(saved.sidecar_path));
  } catch (error) {
    check('preview and annotation methods work over the protocol', false, `${error?.code}: ${error?.message}`);
  } else {
    check('fixture available for preview checks', false, 'run scripts/e2e-video.py --fixture-only');
  }

  const download = await client.request('model.download.status', {});
  check('model download reports installed models', download.installed === true, String(download.installed));
  try {
    await client.request('model.download.start', { model_id: 'realesrgan_general_x4v3' });
    check('a model with no release is refused', false, 'it resolved');
  } catch (error) {
    check('a model with no release is refused', error.code === 'NOT_IMPLEMENTED', error.code);
  }


  section('6. upscale capabilities name their reasons');
  const upscaling = await client.request('upscale.capabilities', {});
  check('both engines are reported', Array.isArray(upscaling.engines) && upscaling.engines.length === 2,
    (upscaling.engines || []).map((entry) => entry.engine).join(','));
  const realesrgan = upscaling.engines.find((entry) => entry.engine === 'realesrgan');
  const seedvr = upscaling.engines.find((entry) => entry.engine === 'seedvr2');
  check('realesrgan reports its model state', typeof realesrgan.available === 'boolean');
  check('an unavailable engine carries a reason',
    seedvr.available === true || Boolean(seedvr.reason), String(seedvr.reason || '').slice(0, 40));
  check('every model names its engine',
    upscaling.models.every((entry) => entry.engine && entry.model_id));
  check('the configuration surface is advertised',
    upscaling.modes.includes('enhance_same_resolution')
      && upscaling.target_presets.includes('1080p')
      && upscaling.same_res_strengths.includes('x2_then_downscale'));
  check('the seedvr runtime block is present', typeof upscaling.seedvr === 'object');

  section('4. structured errors');
  try {
    await client.request('model.install', { model_id: 'lama_roi' });
    check('an unknown detector model is refused', false, 'it resolved instead');
  } catch (error) {
    check('an unknown detector model is refused', error instanceof EngineError);
    check('carries INVALID_PARAMS', error.code === 'INVALID_PARAMS', error.code);
    check('is not retryable', error.retryable === false);
  }

  try {
    await client.request('system.ping', { bogus: 1 });
    check('unexpected param rejects', false, 'it resolved instead');
  } catch (error) {
    check('unexpected param rejects', error.code === 'INVALID_PARAMS', error.code);
    check(
      'detail names the offending property',
      JSON.stringify(error.detail).includes('bogus'),
      JSON.stringify(error.detail),
    );
  }

  try {
    await client.request('does.not.exist', {});
    check('unknown method rejects', false, 'it resolved instead');
  } catch (error) {
    check('unknown method rejects', error.code === 'METHOD_NOT_FOUND', error.code);
  }

  // The RPC surface is stricter than the sidecar reader on purpose: sidecars are
  // hand-editable files that must be tolerated, RPC params come from our own UI
  // and a malformed shape means a shell bug we want surfaced immediately.
  try {
    await client.request('annotation.normalize', {
      segments: ['not-a-segment'],
      video: { width: 1920, height: 1080, frame_count: 900 },
    });
    check('malformed segment rejected by the contract', false, 'it resolved instead');
  } catch (error) {
    check('malformed segment rejected by the contract', error.code === 'INVALID_PARAMS', error.code);
  }

  section('5. clean shutdown');
  await client.shutdown();
  liveClients.delete(client);
  check('client reports not running', client.isRunning === false);
  check('no pending requests left', client.pendingCount === 0);

  // ------------------------------------------------------- stderr noise attack
  section('6. stderr noise cannot corrupt the protocol');
  let noiseLines = 0;
  const noisy = newClient({ ERASER_STDERR_NOISE: '2000' }, () => {
    noiseLines += 1;
  });
  await noisy.start();
  const noisyPing = await noisy.request('system.ping', {});
  check('ping still works with 2000 log lines', noisyPing.pong === true);
  const noisyCaps = await noisy.request('system.capabilities', {});
  check('capabilities still parse', Array.isArray(noisyCaps.methods));
  check('the noise really was emitted', noiseLines >= 1000, `${noiseLines} lines`);
  await noisy.shutdown();
  liveClients.delete(noisy);
  check('noisy engine shut down', noisy.isRunning === false);

  // ------------------------------------------------------------ crash handling
  section('7. a dead engine fails loudly, not silently');
  const broken = new EngineClient({
    command: PYTHON,
    args: ['-c', 'import sys; sys.stderr.write("boom\\n"); sys.exit(3)'],
    cwd: repoRoot,
    readyTimeoutMs: 8_000,
  });
  try {
    await broken.start();
    check('dead engine rejects start()', false, 'start() resolved');
  } catch (error) {
    check('dead engine rejects start()', error instanceof EngineError);
    check('reports ENGINE_EXITED', error.code === 'ENGINE_EXITED', error.code);
  }
  broken.dispose();

  // ------------------------------------------------------------------ summary
  const passed = results.length - failures;
  console.log(`\n${passed}/${results.length} checks passed`);
  if (failures > 0) {
    console.error(`${failures} check(s) failed`);
    process.exitCode = 1;
  }
}

try {
  await main();
} catch (error) {
  console.error('\nharness crashed:', error);
  process.exitCode = 1;
} finally {
  disposeAll();
}

// Safety net: if a pipe handle somehow survives disposal, do not hang the caller.
setTimeout(() => process.exit(process.exitCode ?? 0), 5_000).unref();
