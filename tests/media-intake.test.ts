import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { eq } from 'drizzle-orm';
import { newId, type AppDb } from '$lib/server/db/client';
import { connections, draftMedia, drafts, users } from '$lib/server/db/schema';
import { createTestDb, createTestMedia, TEST_ENV } from '$lib/server/db/test';
import { MEDIA_RECEIPT_SCHEMA, parseMediaReceipt } from '$lib/domain/media-receipt';
import { POST as intakePOST } from '../src/routes/api/drafts/+server';
import { POST as mediaPOST } from '../src/routes/api/drafts/[id]/media/+server';
import { POST as approvePOST } from '../src/routes/api/drafts/[id]/approval/+server';
import { pushMedia } from '../scripts/lib/media-push.mjs';

// An `ftyp` box header followed by payload bytes, enough for the MP4 sniffer.
function mp4(payload: string): Uint8Array {
	const head = [0, 0, 0, 0x18, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d];
	return new Uint8Array([...head, ...new TextEncoder().encode(payload)]);
}
const sha = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

function receiptFor(bytes: Uint8Array, generator = { kind: 'cloud', name: 'clip-agent' }) {
	return {
		schema: MEDIA_RECEIPT_SCHEMA,
		generator,
		output: { sha256: sha(bytes), bytes: bytes.length, mime: 'video/mp4', durationSeconds: 21 },
		sources: [
			{
				title: 'Founder interview',
				creator: 'Example Show',
				sourceUrl: 'https://example.com/episode-4',
				license: 'CC BY 4.0',
				licenseUrl: 'https://creativecommons.org/licenses/by/4.0/'
			}
		]
	};
}

describe('media receipts', () => {
	it('accepts a generator receipt and keeps only bounded, credited sources', () => {
		const bytes = mp4('a');
		const receipt = receiptFor(bytes);
		receipt.sources.push({ title: 'Script', sourceUrl: 'javascript:alert(1)' } as never);
		const parsed = parseMediaReceipt(receipt);
		expect(parsed).toMatchObject({
			ok: true,
			value: {
				generator: { kind: 'cloud', name: 'clip-agent' },
				sha256: sha(bytes),
				bytes: bytes.length,
				mime: 'video/mp4',
				durationSeconds: 21,
				sources: [{ title: 'Founder interview', sourceUrl: 'https://example.com/episode-4' }]
			}
		});
	});

	it('reads an approved Mashup export as a local generator and credits upstream sources', () => {
		const bytes = mp4('mashup');
		const parsed = parseMediaReceipt({
			schema: 'fleet.mashup-media-receipt.v1',
			artifactId: 'edit-42',
			approval: { status: 'approved' },
			output: { video: { sha256: sha(bytes), bytes: bytes.length }, durationSeconds: 30 },
			sources: [
				{
					title: 'Compilation',
					sourceUrl: 'urn:fleet:compilation',
					upstreamSources: [{ title: 'Talk', sourceUrl: 'https://example.com/talk' }]
				}
			]
		});
		expect(parsed).toMatchObject({
			ok: true,
			value: {
				generator: { kind: 'local', name: 'mashup', runId: 'edit-42' },
				sources: [{ title: 'Talk', sourceUrl: 'https://example.com/talk' }]
			}
		});
		expect(
			parseMediaReceipt({
				schema: 'fleet.mashup-media-receipt.v1',
				approval: { status: 'pending' },
				output: { video: { sha256: sha(bytes), bytes: bytes.length } }
			}).ok
		).toBe(false);
	});

	it('refuses unknown schemas, generator kinds and digests', () => {
		const bytes = mp4('b');
		expect(parseMediaReceipt({ ...receiptFor(bytes), schema: 'other' }).ok).toBe(false);
		expect(parseMediaReceipt(receiptFor(bytes, { kind: 'magic', name: 'x' })).ok).toBe(false);
		expect(
			parseMediaReceipt({ ...receiptFor(bytes), output: { sha256: 'abc', bytes: 1 } }).ok
		).toBe(false);
	});
});

describe('generated media intake', () => {
	let db: AppDb;
	let close: () => void;
	let userId: string;
	let connId: string;
	const media = createTestMedia();
	const env = { ...TEST_ENV, videoUploadEnabled: true };
	const user = () => ({
		id: userId,
		email: 'fleet@localhost',
		timezone: 'UTC',
		totpEnabled: true,
		mfaVerified: true
	});

	beforeAll(async () => {
		({ db, close } = await createTestDb());
		const now = new Date();
		userId = newId();
		connId = newId();
		await db.insert(users).values({
			id: userId,
			email: 'fleet@localhost',
			passwordHash: 'x',
			timezone: 'UTC',
			createdAt: now,
			updatedAt: now
		});
		await db.insert(connections).values({
			id: connId,
			userId,
			platform: 'youtube',
			handle: 'fleet',
			credentialsEncrypted: 'enc',
			status: 'active',
			createdAt: now,
			updatedAt: now
		});
	});
	afterAll(() => close());

	async function createDraft(sourceRef: string, scopes = ['intake']) {
		const response = (await intakePOST({
			request: new Request('http://localhost/api/drafts', {
				method: 'POST',
				body: JSON.stringify({ projectId: 'codevetter', sourceRef, baseBody: 'Clip caption' })
			}),
			locals: { db, user: user(), apiKeyScopes: scopes }
		} as never)) as Response;
		return (await response.json()).draft.id as string;
	}

	async function upload(
		draftId: string,
		bytes: Uint8Array,
		receipt: unknown,
		scopes: string[] | null = ['intake']
	) {
		const form = new FormData();
		form.set('file', new Blob([bytes as BlobPart], { type: 'video/mp4' }), 'clip.mp4');
		if (receipt !== undefined) form.set('receipt', JSON.stringify(receipt));
		return mediaPOST({
			params: { id: draftId },
			request: new Request('http://localhost/api/drafts/x/media', { method: 'POST', body: form }),
			locals: { db, user: user(), apiKeyScopes: scopes, env, media }
		} as never) as Promise<Response>;
	}

	it('stores a receipted video from an intake key and treats a retried push as the same file', async () => {
		const draftId = await createDraft('clip-agent:run-1');
		const bytes = mp4('first clip');
		const first = await upload(draftId, bytes, receiptFor(bytes));
		expect(first.status).toBe(201);
		const created = (await first.json()).media;
		expect(created).toMatchObject({
			sha256: sha(bytes),
			provenance: { generator: { kind: 'cloud', name: 'clip-agent' } }
		});
		expect(await media.get(created.storageKey)).toEqual(bytes);

		const retry = await upload(draftId, bytes, receiptFor(bytes));
		expect(retry.status).toBe(200);
		expect(await retry.json()).toMatchObject({ existing: true, media: { id: created.id } });
		expect(await db.select().from(draftMedia).where(eq(draftMedia.draftId, draftId))).toHaveLength(
			1
		);
	});

	it('refuses bytes that do not match the receipt, and intake media without one', async () => {
		const draftId = await createDraft('clip-agent:run-2');
		const bytes = mp4('real clip');
		const tampered = mp4('different clip');
		const mismatch = await upload(draftId, tampered, {
			...receiptFor(bytes),
			output: { sha256: sha(bytes), bytes: tampered.length }
		});
		expect(mismatch.status).toBe(400);
		expect((await mismatch.json()).error).toMatch(/does not match/);
		expect((await upload(draftId, bytes, undefined)).status).toBe(400);
		expect(await db.select().from(draftMedia).where(eq(draftMedia.draftId, draftId))).toHaveLength(
			0
		);
	});

	it('keeps intake keys off owner drafts and drafts already in review', async () => {
		const bytes = mp4('owner');
		const now = new Date();
		const ownerDraft = newId();
		await db.insert(drafts).values({
			id: ownerDraft,
			userId,
			baseBody: 'Owner draft',
			projectId: 'codevetter',
			createdAt: now,
			updatedAt: now
		});
		expect((await upload(ownerDraft, bytes, receiptFor(bytes))).status).toBe(403);

		const reviewed = await createDraft('clip-agent:run-3');
		await db
			.update(drafts)
			.set({ selectedConnectionIds: JSON.stringify([connId]) })
			.where(eq(drafts.id, reviewed));
		const approved = (await approvePOST({
			params: { id: reviewed },
			locals: { db, user: user(), authMethod: 'session' }
		} as never)) as Response;
		expect(approved.status).toBe(200);
		expect((await upload(reviewed, bytes, receiptFor(bytes))).status).toBe(409);
	});

	it('still lets the owner upload without a receipt', async () => {
		const draftId = await createDraft('owner:manual', ['write']);
		const response = await upload(draftId, mp4('manual'), undefined, null);
		expect(response.status).toBe(201);
		expect((await response.json()).media).toMatchObject({ sha256: null, provenance: null });
	});

	it('pushes a generated file end to end through the CLI client', async () => {
		const dir = mkdtempSync(join(tmpdir(), 'media-push-'));
		const file = join(dir, 'clip.mp4');
		writeFileSync(file, mp4('pushed clip'));
		const seen: string[] = [];
		const origins: (string | null)[] = [];
		const routed = (async (input: string, init: RequestInit) => {
			const request = new Request(input, init);
			seen.push(request.headers.get('authorization') ?? '');
			const locals = { db, user: user(), apiKeyScopes: ['intake'], env, media };
			const path = new URL(input).pathname;
			const mediaPath = path.match(/^\/api\/drafts\/([^/]+)\/media$/);
			if (mediaPath) origins.push(request.headers.get('origin'));
			if (mediaPath) return mediaPOST({ params: { id: mediaPath[1] }, request, locals } as never);
			return intakePOST({ request, locals } as never);
		}) as unknown as typeof fetch;
		const options = {
			baseUrl: 'https://social.example/',
			apiKey: 'fleet-intake-key',
			projectId: 'codevetter',
			sourceRef: 'clip-agent:run-4',
			file,
			generator: 'clip-agent',
			kind: 'api',
			fetch: routed
		};
		const first = await pushMedia(options);
		expect(first).toMatchObject({ draftExisted: false, mediaExisted: false });
		expect(first.reviewUrl).toBe(`https://social.example/review/${first.draftId}`);
		expect(await pushMedia(options)).toMatchObject({
			draftId: first.draftId,
			draftExisted: true,
			mediaExisted: true
		});
		expect(new Set(seen)).toEqual(new Set(['Bearer fleet-intake-key']));
		// Without its own Origin, SvelteKit would refuse the multipart POST.
		expect(new Set(origins)).toEqual(new Set(['https://social.example']));
		const [row] = await db.select().from(draftMedia).where(eq(draftMedia.draftId, first.draftId));
		expect(JSON.parse(row.provenanceJson ?? '{}')).toMatchObject({
			generator: { kind: 'api', name: 'clip-agent', runId: 'clip-agent:run-4' }
		});
	});
});
