/**
 * The `eraser-media:` scheme.
 *
 * Kept out of `main.ts` so other entry points (the smoke driver) can register it
 * without importing the app entry — importing that file runs its top-level setup,
 * and registering the scheme twice is a hard failure.
 */

import { net, protocol } from 'electron';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { MEDIA_SCHEME } from '../../shared/ipc';

/**
 * Declare the scheme before `app.whenReady()`: Chromium has to know it is a
 * real, secure origin that can be used as a media source.
 */
export function declareMediaScheme(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: MEDIA_SCHEME,
      privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
    },
  ]);
}

/** Serve exactly one file per request: the one the URL points at. */
export function registerMediaProtocol(): void {
  protocol.handle(MEDIA_SCHEME, async (request) => {
    let relative: string;
    try {
      relative = decodeURIComponent(new URL(request.url).pathname.replace(/^\//, ''));
    } catch {
      return new Response('bad request', { status: 400 });
    }
    if (!relative) {
      return new Response('not found', { status: 404 });
    }
    // `net.fetch` on a file URL honours Range requests, which is what makes
    // seeking in the preview work instead of re-reading the whole file.
    return net.fetch(pathToFileURL(path.resolve(relative)).toString(), { headers: request.headers });
  });
}
