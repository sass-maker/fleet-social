#!/usr/bin/env node
/**
 * Push a generated video or image into Fleet Social for review.
 *
 * Usage:
 *   npm run media:push -- --project <id> --source-ref <ref> --file clip.mp4 --receipt media-receipt.json
 *   npm run media:push -- --project <id> --source-ref <ref> --file clip.mp4 --generator my-agent --kind local
 *
 * Reads FLEET_SOCIAL_URL and FLEET_SOCIAL_API_KEY (an intake key from Settings),
 * plus CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET when the instance is
 * behind Cloudflare Access.
 */
import { readFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import * as ui from './lib/cli.mjs';
import { pushMedia } from './lib/media-push.mjs';

const { values } = parseArgs({
	options: {
		url: { type: 'string' },
		project: { type: 'string' },
		'source-ref': { type: 'string' },
		file: { type: 'string' },
		receipt: { type: 'string' },
		generator: { type: 'string' },
		kind: { type: 'string', default: 'local' },
		version: { type: 'string' },
		title: { type: 'string' },
		caption: { type: 'string' },
		'caption-file': { type: 'string' },
		verbose: { type: 'boolean' },
		'no-color': { type: 'boolean' }
	}
});

const env = process.env;
const baseUrl = values.url ?? env.FLEET_SOCIAL_URL;
const apiKey = env.FLEET_SOCIAL_API_KEY;
const missing = [
	!baseUrl && '--url or FLEET_SOCIAL_URL',
	!apiKey && 'FLEET_SOCIAL_API_KEY',
	!values.project && '--project',
	!values['source-ref'] && '--source-ref',
	!values.file && '--file'
].filter(Boolean);
if (missing.length) {
	ui.error(`Missing ${missing.join(', ')}`);
	process.exit(1);
}

try {
	const caption = values['caption-file']
		? await readFile(values['caption-file'], 'utf8')
		: values.caption;
	const result = await pushMedia({
		baseUrl: /** @type {string} */ (baseUrl),
		apiKey: /** @type {string} */ (apiKey),
		accessClientId: env.CF_ACCESS_CLIENT_ID,
		accessClientSecret: env.CF_ACCESS_CLIENT_SECRET,
		projectId: /** @type {string} */ (values.project),
		sourceRef: /** @type {string} */ (values['source-ref']),
		file: /** @type {string} */ (values.file),
		receiptPath: values.receipt,
		generator: values.generator,
		kind: values.kind,
		version: values.version,
		title: values.title,
		caption,
		onStep: ui.ok
	});
	ui.line(`Review: ${ui.url(result.reviewUrl)}`);
} catch (err) {
	ui.error(err instanceof Error ? err.message : String(err));
	process.exit(1);
}
