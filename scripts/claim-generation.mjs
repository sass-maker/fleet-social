#!/usr/bin/env node
/**
 * Collect the generation slots due for a local generator.
 *
 * Usage:
 *   npm run generation:claim -- --generator mashup
 *   npm run generation:claim -- --generator mashup --json
 *
 * Render each request, then push the result with its sourceRef and project:
 *   npm run media:push -- --project <projectId> --source-ref <sourceRef> --file out.mp4 --receipt receipt.json
 *
 * Reads the same FLEET_SOCIAL_URL, FLEET_SOCIAL_API_KEY and Access variables as media:push.
 */
import { parseArgs } from 'node:util';
import * as ui from './lib/cli.mjs';
import { claimGeneration } from './lib/media-push.mjs';

const { values } = parseArgs({
	options: {
		url: { type: 'string' },
		generator: { type: 'string' },
		json: { type: 'boolean' },
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
	!values.generator && '--generator'
].filter(Boolean);
if (missing.length) {
	ui.error(`Missing ${missing.join(', ')}`);
	process.exit(1);
}

try {
	const requests = await claimGeneration({
		baseUrl: /** @type {string} */ (baseUrl),
		apiKey: /** @type {string} */ (apiKey),
		accessClientId: env.CF_ACCESS_CLIENT_ID,
		accessClientSecret: env.CF_ACCESS_CLIENT_SECRET,
		generator: /** @type {string} */ (values.generator)
	});
	if (values.json) {
		process.stdout.write(`${JSON.stringify(requests, null, 2)}\n`);
	} else if (!requests.length) {
		ui.ok('Nothing due');
	} else {
		for (const request of requests) {
			ui.ok(`${request.intake.sourceRef} · ${request.projectId} · release ${request.plannedFor}`);
			ui.note(`  ${request.title ?? request.brief.slice(0, 100)}`);
		}
	}
} catch (err) {
	ui.error(err instanceof Error ? err.message : String(err));
	process.exit(1);
}
