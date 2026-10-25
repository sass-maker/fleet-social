import { createHash, createHmac } from 'node:crypto';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { eq } from 'drizzle-orm';
import { newId, type AppDb } from '$lib/server/db/client';
import {
	connections,
	generationSlots,
	generators,
	publishTargets,
	users
} from '$lib/server/db/schema';
import { createTestDb, createTestMedia, TEST_ENV } from '$lib/server/db/test';
import { isFleetProjectId } from '$lib/domain/fleet-projects';
import { parseGeneratorInput, parseSlotInput, slotSourceRef } from '$lib/domain/generation';
import { claimSlots, createGenerator, createSlot, dispatchDueSlots } from '$lib/server/generation';
import { loadPlanner } from '$lib/server/planner';
import { runSchedulerTick } from '$lib/server/scheduler';
import { POST as generatorsPOST } from '../src/routes/api/generators/+server';
import { PATCH as slotPATCH } from '../src/routes/api/generation/slots/[id]/+server';
import { POST as claimPOST } from '../src/routes/api/generation/claim/+server';
import { POST as intakePOST } from '../src/routes/api/drafts/+server';
import { POST as mediaPOST } from '../src/routes/api/drafts/[id]/media/+server';

const HOUR = 60 * 60_000;
const mp4 = (payload: string) =>
	new Uint8Array([
		...[0, 0, 0, 0x18, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d],
		...new TextEncoder().encode(payload)
	]);
const sha = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');

describe('generation input', () => {
	it('needs a reachable https webhook for cloud and API generators only', () => {
		expect(parseGeneratorInput({ name: 'mashup', kind: 'local' })).toMatchObject({
			ok: true,
			value: { endpointUrl: null, leadMinutes: 360 }
		});
		expect(parseGeneratorInput({ name: 'render', kind: 'cloud' }).ok).toBe(false);
		expect(
			parseGeneratorInput({ name: 'render', kind: 'cloud', endpointUrl: 'http://example.com/x' }).ok
		).toBe(false);
		expect(
			parseGeneratorInput({ name: 'render', kind: 'api', endpointUrl: 'http://localhost:8787/x' })
				.ok
		).toBe(true);
		expect(parseGeneratorInput({ name: 'bad name', kind: 'local' }).ok).toBe(false);
	});

	it('plans releases in the future for an active project', () => {
		const now = new Date('2026-11-01T00:00:00Z');
		const base = { generatorId: 'g', projectId: 'codevetter', brief: 'Clip' };
		expect(
			parseSlotInput({ ...base, plannedFor: '2026-11-02T10:00:00Z' }, isFleetProjectId, now).ok
		).toBe(true);
		expect(
			parseSlotInput({ ...base, plannedFor: '2026-10-30T10:00:00Z' }, isFleetProjectId, now).ok
		).toBe(false);
		expect(
			parseSlotInput(
				{ ...base, projectId: 'nope', plannedFor: '2026-11-02T10:00:00Z' },
				isFleetProjectId,
				now
			).ok
		).toBe(false);
	});
});

describe('scheduled generation', () => {
	let db: AppDb;
	let close: () => void;
	let userId: string;
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
		userId = newId();
		const now = new Date();
		await db.insert(users).values({
			id: userId,
			email: 'fleet@localhost',
			passwordHash: 'x',
			timezone: 'UTC',
			createdAt: now,
			updatedAt: now
		});
	});
	afterAll(() => close());

	async function slotRow(id: string) {
		const [row] = await db.select().from(generationSlots).where(eq(generationSlots.id, id));
		return row;
	}

	it('registers webhook generators with a secret kept only encrypted, through the session', async () => {
		const request = (authMethod: string) =>
			generatorsPOST({
				request: new Request('http://localhost/api/generators', {
					method: 'POST',
					body: JSON.stringify({
						name: 'cloud-render',
						kind: 'cloud',
						endpointUrl: 'https://render.example/jobs'
					})
				}),
				locals: { db, user: user(), authMethod, env }
			} as never) as Promise<Response>;
		expect((await request('bearer')).status).toBe(401);
		const created = await request('session');
		expect(created.status).toBe(201);
		const { generator, secret } = await created.json();
		expect(secret).toMatch(/^[0-9a-f]{64}$/);
		const [row] = await db.select().from(generators).where(eq(generators.id, generator.id));
		expect(row.secretEnc).toBeTruthy();
		expect(row.secretEnc).not.toContain(secret);
		expect((await request('session')).status).toBe(409);
	});

	it('requests a cloud generator ahead of release with a signed webhook, backing off on failure', async () => {
		const now = new Date();
		const { generator, secret } = await createGenerator(db, env, userId, {
			name: 'api-clipper',
			kind: 'api',
			endpointUrl: 'https://clipper.example/requests',
			leadMinutes: 120
		});
		const slot = await createSlot(
			db,
			userId,
			{
				generatorId: generator.id,
				projectId: 'codevetter',
				brief: 'A clip about release notes',
				title: 'Release notes',
				plannedFor: new Date(now.getTime() + 3 * HOUR)
			},
			now
		);
		expect(new Date(slot.requestAt).getTime()).toBe(now.getTime() + HOUR);
		expect(await dispatchDueSlots(db, env, { now, fetchImpl: never })).toEqual([]);

		const due = new Date(now.getTime() + HOUR + 1);
		const failing = (async () => new Response('busy', { status: 503 })) as typeof fetch;
		expect(await dispatchDueSlots(db, env, { now: due, fetchImpl: failing })).toEqual([
			{ id: slot.id, status: 'planned' }
		]);
		const backedOff = await slotRow(slot.id);
		expect(backedOff).toMatchObject({ attempts: 1, lastError: 'Generator answered HTTP 503' });
		expect(backedOff.requestAt.getTime()).toBeGreaterThan(due.getTime());

		const later = new Date(backedOff.requestAt.getTime() + 1);
		let received: { headers: Headers; body: string } | null = null;
		const accepting = (async (_url: string, init: RequestInit) => {
			received = { headers: new Headers(init.headers), body: String(init.body) };
			return new Response(null, { status: 202 });
		}) as unknown as typeof fetch;
		expect(await dispatchDueSlots(db, env, { now: later, fetchImpl: accepting })).toEqual([
			{ id: slot.id, status: 'requested' }
		]);
		const sent = received as unknown as { headers: Headers; body: string };
		const timestamp = sent.headers.get('x-fleet-timestamp');
		const expected = createHmac('sha256', secret as string)
			.update(`${timestamp}.${sent.body}`)
			.digest('hex');
		expect(sent.headers.get('x-fleet-signature')).toBe(`sha256=${expected}`);
		expect(JSON.parse(sent.body)).toMatchObject({
			schema: 'fleet.generation-request.v1',
			slotId: slot.id,
			projectId: 'codevetter',
			brief: 'A clip about release notes',
			intake: { sourceRef: slotSourceRef(slot.id), draftsUrl: 'http://localhost:5173/api/drafts' }
		});
		expect((await slotRow(slot.id)).status).toBe('requested');
	});

	it('parks a slot in failed after repeated webhook failures and lets the owner ask again', async () => {
		const now = new Date();
		const { generator } = await createGenerator(db, env, userId, {
			name: 'flaky',
			kind: 'cloud',
			endpointUrl: 'https://flaky.example/',
			leadMinutes: 3 * 24 * 60
		});
		const slot = await createSlot(
			db,
			userId,
			{
				generatorId: generator.id,
				projectId: 'codevetter',
				brief: 'Flaky',
				title: null,
				plannedFor: new Date(now.getTime() + 48 * HOUR)
			},
			now
		);
		const down = (async () => {
			throw new Error('connect ECONNREFUSED');
		}) as typeof fetch;
		let at = now;
		for (let i = 0; i < 5; i++) {
			await dispatchDueSlots(db, env, { now: at, fetchImpl: down });
			at = new Date((await slotRow(slot.id)).requestAt.getTime() + 1);
		}
		expect(await slotRow(slot.id)).toMatchObject({ status: 'failed', attempts: 5 });
		const retried = (await slotPATCH({
			params: { id: slot.id },
			request: new Request('http://localhost/x', {
				method: 'PATCH',
				body: JSON.stringify({ action: 'retry' })
			}),
			locals: { db, user: user(), apiKeyScopes: null }
		} as never)) as Response;
		expect(retried.status).toBe(200);
		expect(await slotRow(slot.id)).toMatchObject({ status: 'planned', attempts: 0 });
		await slotPATCH({
			params: { id: slot.id },
			request: new Request('http://localhost/x', {
				method: 'PATCH',
				body: JSON.stringify({ action: 'cancel' })
			}),
			locals: { db, user: user(), apiKeyScopes: null }
		} as never);
		expect((await slotRow(slot.id)).status).toBe('cancelled');
	});

	it('takes a local generator from claimed slot to media ready for review and onto the calendar', async () => {
		const now = new Date();
		const { generator } = await createGenerator(db, env, userId, {
			name: 'mashup',
			kind: 'local',
			endpointUrl: null,
			leadMinutes: 60
		});
		const soon = await createSlot(db, userId, {
			generatorId: generator.id,
			projectId: 'codevetter',
			brief: 'Cut the founder interview into a short',
			title: 'Founder short',
			plannedFor: new Date(now.getTime() + 30 * 60_000)
		});
		const later = await createSlot(db, userId, {
			generatorId: generator.id,
			projectId: 'codevetter',
			brief: 'Next week',
			title: null,
			plannedFor: new Date(now.getTime() + 7 * 24 * HOUR)
		});
		// Local generators are never called; the tick leaves their slots alone.
		expect(
			(await runSchedulerTick(db, env, { store: media, fetchImpl: never })).generation
		).toEqual([]);

		const claim = async () =>
			(await (
				(await claimPOST({
					request: new Request('http://localhost/api/generation/claim', {
						method: 'POST',
						body: JSON.stringify({ generator: 'mashup' })
					}),
					locals: { db, user: user(), apiKeyScopes: ['intake'], env }
				} as never)) as Response
			).json()) as { requests: Array<{ slotId: string; intake: { sourceRef: string } }> };
		const first = await claim();
		expect(first.requests.map((r) => r.slotId)).toEqual([soon.id]);
		expect((await slotRow(soon.id)).status).toBe('requested');
		expect((await slotRow(later.id)).status).toBe('planned');
		// A crashed agent sees its unfinished work again.
		expect((await claim()).requests.map((r) => r.slotId)).toEqual([soon.id]);

		const intake = (body: Record<string, unknown>) =>
			intakePOST({
				request: new Request('http://localhost/api/drafts', {
					method: 'POST',
					body: JSON.stringify(body)
				}),
				locals: { db, user: user(), apiKeyScopes: ['intake'] }
			} as never) as Promise<Response>;
		const sourceRef = first.requests[0].intake.sourceRef;
		expect((await intake({ projectId: 'chess', sourceRef })).status).toBe(400);
		expect((await intake({ projectId: 'codevetter', sourceRef: 'slot:unknown' })).status).toBe(400);
		const created = await intake({ projectId: 'codevetter', sourceRef, baseBody: 'Caption' });
		expect(created.status).toBe(201);
		const draft = (await created.json()).draft;
		expect(draft.title).toBe('Founder short');
		expect((await slotRow(soon.id)).draftId).toBe(draft.id);

		const bytes = mp4('founder short');
		const form = new FormData();
		form.set('file', new Blob([bytes as BlobPart], { type: 'video/mp4' }), 'short.mp4');
		form.set(
			'receipt',
			JSON.stringify({
				schema: 'fleet.media-receipt.v1',
				generator: { kind: 'local', name: 'mashup' },
				output: { sha256: sha(bytes), bytes: bytes.length, mime: 'video/mp4' }
			})
		);
		const uploaded = (await mediaPOST({
			params: { id: draft.id },
			request: new Request('http://localhost/x', { method: 'POST', body: form }),
			locals: { db, user: user(), apiKeyScopes: ['intake'], env, media }
		} as never)) as Response;
		expect(uploaded.status).toBe(201);
		expect(await slotRow(soon.id)).toMatchObject({ status: 'ready', draftId: draft.id });
		expect((await claim()).requests).toEqual([]);

		const planner = await loadPlanner(db, userId);
		expect(planner.events.find((e) => e.id === `slot:${soon.id}`)).toMatchObject({
			status: 'ready for review',
			url: `/review/${draft.id}`
		});
		expect(planner.events.find((e) => e.id === `slot:${later.id}`)).toMatchObject({
			status: 'awaiting generation'
		});

		// Once the owner schedules the delivery, it takes the slot's place.
		const connId = newId();
		const at = new Date();
		await db.insert(connections).values({
			id: connId,
			userId,
			platform: 'youtube',
			handle: 'fleet',
			credentialsEncrypted: 'enc',
			status: 'active',
			createdAt: at,
			updatedAt: at
		});
		await db.insert(publishTargets).values({
			id: newId(),
			draftId: draft.id,
			connectionId: connId,
			status: 'scheduled',
			scheduledFor: soon.plannedFor,
			createdAt: at,
			updatedAt: at
		});
		const scheduled = await loadPlanner(db, userId);
		expect(scheduled.events.some((e) => e.id === `slot:${soon.id}`)).toBe(false);
		expect(scheduled.events.some((e) => e.draftId === draft.id)).toBe(true);
	});

	it('only claims for a generator that exists', async () => {
		await expect(claimSlots(db, userId, 'nobody', TEST_ENV.APP_URL)).rejects.toMatchObject({
			status: 404
		});
	});
});

const never = (async () => {
	throw new Error('No request expected');
}) as unknown as typeof fetch;
