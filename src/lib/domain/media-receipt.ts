/**
 * A media receipt is how any generator — a local clipping agent such as
 * Mashup, a cloud render job, or a third-party generation API — hands a
 * finished file to Fleet Social. It names the generator, pins the exact bytes
 * by SHA-256, and carries source credits. It is provenance, not authority:
 * the owner still approves the saved draft before anything is published.
 */
export const MEDIA_RECEIPT_SCHEMA = 'fleet.media-receipt.v1';
// Mashup's original export format, accepted so existing renders push unchanged.
const MASHUP_RECEIPT_SCHEMA = 'fleet.mashup-media-receipt.v1';

export const GENERATOR_KINDS = ['local', 'cloud', 'api'] as const;
export type GeneratorKind = (typeof GENERATOR_KINDS)[number];

export type SourceCredit = {
	title: string;
	creator?: string;
	sourceUrl: string;
	license?: string;
	licenseUrl?: string;
};

export type MediaProvenance = {
	generator: { kind: GeneratorKind; name: string; version?: string; runId?: string };
	sha256: string;
	bytes: number;
	mime?: string;
	durationSeconds?: number;
	width?: number;
	height?: number;
	sources: SourceCredit[];
};

const MAX_SOURCES = 50;
const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;

type Result = { ok: true; value: MediaProvenance } | { ok: false; error: string };

function text(value: unknown, max: number): string | undefined {
	if (typeof value !== 'string') return undefined;
	const trimmed = value.trim();
	return trimmed ? trimmed.slice(0, max) : undefined;
}

function positive(value: unknown): number | undefined {
	return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined;
}

function sourceCredit(value: unknown): SourceCredit | null {
	if (!value || typeof value !== 'object') return null;
	const raw = value as Record<string, unknown>;
	const title = text(raw.title, 300);
	const sourceUrl = text(raw.sourceUrl, 2000);
	if (!title || !sourceUrl || !/^(https:\/\/|urn:)/.test(sourceUrl)) return null;
	const credit: SourceCredit = { title, sourceUrl };
	const creator = text(raw.creator, 200);
	const license = text(raw.license, 200);
	const licenseUrl = text(raw.licenseUrl, 2000);
	if (creator) credit.creator = creator;
	if (license) credit.license = license;
	if (licenseUrl?.startsWith('https://')) credit.licenseUrl = licenseUrl;
	return credit;
}

/** Mashup credits a compilation's own upstream programmes; those are what a caption cites. */
function flattenSources(value: unknown): unknown[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((source) => {
		const upstream = (source as { upstreamSources?: unknown } | null)?.upstreamSources;
		return Array.isArray(upstream) && upstream.length ? upstream : [source];
	});
}

export function parseMediaReceipt(input: unknown): Result {
	if (!input || typeof input !== 'object') return { ok: false, error: 'receipt must be an object' };
	const raw = input as Record<string, unknown>;
	let generator: MediaProvenance['generator'];
	let output: Record<string, unknown>;
	let file: Record<string, unknown>;
	if (raw.schema === MASHUP_RECEIPT_SCHEMA) {
		// Mashup only exports after its own edit approval; a receipt without it
		// is an unfinished render, not a candidate for review.
		if ((raw.approval as { status?: unknown } | undefined)?.status !== 'approved') {
			return { ok: false, error: 'Mashup receipt is not an approved export' };
		}
		output = (raw.output ?? {}) as Record<string, unknown>;
		file = (output.video ?? {}) as Record<string, unknown>;
		generator = { kind: 'local', name: 'mashup' };
		const runId = text(raw.artifactId, 200);
		if (runId) generator.runId = runId;
	} else if (raw.schema === MEDIA_RECEIPT_SCHEMA) {
		const gen = (raw.generator ?? {}) as Record<string, unknown>;
		if (!GENERATOR_KINDS.includes(gen.kind as GeneratorKind)) {
			return { ok: false, error: 'generator.kind must be local, cloud or api' };
		}
		if (typeof gen.name !== 'string' || !NAME_RE.test(gen.name)) {
			return { ok: false, error: 'generator.name must be a short identifier' };
		}
		generator = { kind: gen.kind as GeneratorKind, name: gen.name };
		const version = text(gen.version, 100);
		const runId = text(gen.runId, 200);
		if (version) generator.version = version;
		if (runId) generator.runId = runId;
		output = (raw.output ?? {}) as Record<string, unknown>;
		file = output;
	} else {
		return { ok: false, error: `receipt schema must be ${MEDIA_RECEIPT_SCHEMA}` };
	}
	const sha256 = typeof file.sha256 === 'string' ? file.sha256.toLowerCase() : '';
	if (!SHA256_RE.test(sha256))
		return { ok: false, error: 'output.sha256 must be a SHA-256 hex digest' };
	const bytes = file.bytes;
	if (typeof bytes !== 'number' || !Number.isInteger(bytes) || bytes <= 0) {
		return { ok: false, error: 'output.bytes must be the file size in bytes' };
	}
	const sources = flattenSources(raw.sources)
		.map(sourceCredit)
		.filter((source): source is SourceCredit => source !== null);
	const unique = [...new Map(sources.map((source) => [source.sourceUrl, source])).values()];
	const value: MediaProvenance = {
		generator,
		sha256,
		bytes,
		sources: unique.slice(0, MAX_SOURCES)
	};
	const mime = text(file.mime, 100);
	const durationSeconds = positive(output.durationSeconds);
	const width = positive(output.width);
	const height = positive(output.height);
	if (mime) value.mime = mime.toLowerCase();
	if (durationSeconds) value.durationSeconds = durationSeconds;
	if (width) value.width = Math.round(width);
	if (height) value.height = Math.round(height);
	return { ok: true, value };
}

export function parseStoredProvenance(raw: string | null | undefined): MediaProvenance | null {
	if (!raw) return null;
	try {
		const value = JSON.parse(raw) as MediaProvenance;
		return value && typeof value === 'object' && value.generator ? value : null;
	} catch {
		return null;
	}
}
