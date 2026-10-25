/**
 * Push one generated file into Fleet Social as a draft awaiting review.
 *
 * Any generator uses the same two calls: create (or find) the draft by its
 * stable `sourceRef`, then attach the file with its media receipt. Both are
 * idempotent, so a generator can re-run a push after a dropped connection
 * without creating a second draft or storing the bytes twice.
 */
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { basename, extname } from 'node:path';

export const MEDIA_RECEIPT_SCHEMA = 'fleet.media-receipt.v1';

const MIME_BY_EXT = {
	'.mp4': 'video/mp4',
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.webp': 'image/webp',
	'.gif': 'image/gif'
};

/** @param {string} path */
export function mimeFor(path) {
	const mime = MIME_BY_EXT[/** @type {keyof typeof MIME_BY_EXT} */ (extname(path).toLowerCase())];
	if (!mime) throw new Error(`Unsupported file type: ${basename(path)} (mp4, png, jpg, webp, gif)`);
	return mime;
}

/** @param {Uint8Array} bytes */
export const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

/**
 * The receipt digest a generator wrote, wherever its schema keeps it.
 * @param {any} receipt
 */
function receiptDigest(receipt) {
	return receipt?.output?.video?.sha256 ?? receipt?.output?.sha256;
}

/**
 * A receipt for a generator that does not write its own.
 * @param {{ bytes: Uint8Array, mime: string, generator: string, kind: string, version?: string, runId?: string }} input
 */
export function buildReceipt({ bytes, mime, generator, kind, version, runId }) {
	return {
		schema: MEDIA_RECEIPT_SCHEMA,
		generator: {
			kind,
			name: generator,
			...(version ? { version } : {}),
			...(runId ? { runId } : {})
		},
		output: { sha256: sha256(bytes), bytes: bytes.length, mime },
		sources: []
	};
}

/**
 * @param {{
 *   baseUrl: string,
 *   apiKey: string,
 *   accessClientId?: string,
 *   accessClientSecret?: string,
 *   projectId: string,
 *   sourceRef: string,
 *   file: string,
 *   receiptPath?: string,
 *   generator?: string,
 *   kind?: string,
 *   version?: string,
 *   title?: string,
 *   caption?: string,
 *   fetch?: typeof fetch,
 *   onStep?: (text: string) => void
 * }} options
 */
export async function pushMedia(options) {
	const doFetch = options.fetch ?? fetch;
	const step = options.onStep ?? (() => {});
	const base = options.baseUrl.replace(/\/+$/, '');
	const headers = authHeaders(options);

	const bytes = new Uint8Array(await readFile(options.file));
	const mime = mimeFor(options.file);
	let receipt;
	if (options.receiptPath) {
		receipt = JSON.parse(await readFile(options.receiptPath, 'utf8'));
		// Fail here rather than after uploading 95MB to be told the same thing.
		if (receiptDigest(receipt) !== sha256(bytes)) {
			throw new Error(`${basename(options.file)} does not match ${basename(options.receiptPath)}`);
		}
	} else {
		if (!options.generator) throw new Error('Pass --receipt, or --generator to describe the file');
		receipt = buildReceipt({
			bytes,
			mime,
			generator: options.generator,
			kind: options.kind ?? 'local',
			version: options.version,
			runId: options.sourceRef
		});
	}
	step(
		`${basename(options.file)} · ${(bytes.length / 1_000_000).toFixed(1)} MB · sha256 ${sha256(bytes).slice(0, 12)}`
	);

	const draftResponse = await doFetch(`${base}/api/drafts`, {
		method: 'POST',
		headers: { ...headers, 'Content-Type': 'application/json' },
		body: JSON.stringify({
			projectId: options.projectId,
			sourceRef: options.sourceRef,
			...(options.title ? { title: options.title } : {}),
			...(options.caption ? { baseBody: options.caption } : {})
		})
	});
	const draftBody = await readJson(draftResponse);
	if (!draftResponse.ok) throw new Error(`Draft: ${draftBody.error ?? draftResponse.status}`);
	const draftId = draftBody.draft.id;
	step(`${draftBody.existing ? 'Found' : 'Created'} draft ${draftId}`);

	const form = new FormData();
	form.set('file', new Blob([bytes], { type: mime }), basename(options.file));
	form.set('receipt', JSON.stringify(receipt));
	const mediaResponse = await doFetch(`${base}/api/drafts/${draftId}/media`, {
		method: 'POST',
		// SvelteKit refuses any multipart POST whose Origin is not the app's
		// own, before auth runs, and a missing Origin counts as foreign.
		headers: { ...headers, Origin: new URL(base).origin },
		body: form
	});
	const mediaBody = await readJson(mediaResponse);
	if (!mediaResponse.ok) throw new Error(`Media: ${mediaBody.error ?? mediaResponse.status}`);
	step(mediaBody.existing ? 'Media already attached' : 'Media attached and verified');

	return {
		draftId,
		draftExisted: Boolean(draftBody.existing),
		mediaExisted: Boolean(mediaBody.existing),
		reviewUrl: `${base}/review/${draftId}`
	};
}

/**
 * Due generation slots for a generator the Worker cannot call. Each request
 * names the sourceRef and project to push the finished media under.
 * @param {{ baseUrl: string, apiKey: string, accessClientId?: string, accessClientSecret?: string, generator: string, fetch?: typeof fetch }} options
 */
export async function claimGeneration(options) {
	const doFetch = options.fetch ?? fetch;
	const base = options.baseUrl.replace(/\/+$/, '');
	const response = await doFetch(`${base}/api/generation/claim`, {
		method: 'POST',
		headers: { ...authHeaders(options), 'Content-Type': 'application/json' },
		body: JSON.stringify({ generator: options.generator })
	});
	const body = await readJson(response);
	if (!response.ok) throw new Error(`Claim: ${body.error ?? response.status}`);
	return body.requests;
}

/** @param {{ apiKey: string, accessClientId?: string, accessClientSecret?: string }} options */
function authHeaders(options) {
	/** @type {Record<string, string>} */
	const headers = { Authorization: `Bearer ${options.apiKey}` };
	// A deployment behind Cloudflare Access needs a service token on every
	// machine call, alongside the app's own key.
	if (options.accessClientId && options.accessClientSecret) {
		headers['CF-Access-Client-Id'] = options.accessClientId;
		headers['CF-Access-Client-Secret'] = options.accessClientSecret;
	}
	return headers;
}

/** @param {Response} response */
async function readJson(response) {
	try {
		return await response.json();
	} catch {
		// Access answers an unauthenticated call with its HTML login page.
		return { error: `HTTP ${response.status} (not JSON — check the Access service token)` };
	}
}
