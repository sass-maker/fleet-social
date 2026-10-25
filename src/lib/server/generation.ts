import { and, asc, eq, inArray, isNotNull, lte, ne, or } from 'drizzle-orm';
import { randomHex } from '$lib/domain/bytes';
import {
	GENERATION_REQUEST_SCHEMA,
	MAX_DISPATCH_ATTEMPTS,
	retryDelayMinutes,
	slotIdFromSourceRef,
	slotSourceRef,
	type GeneratorInput,
	type SlotInput
} from '$lib/domain/generation';
import type { SubrequestBudget } from './budget';
import { decryptSecret, encryptSecret, hmacHex } from './crypto';
import { first, newId, type AppDb } from './db/client';
import { generationSlots, generators } from './db/schema';
import type { AppEnv } from './env';

type GeneratorRow = typeof generators.$inferSelect;
type SlotRow = typeof generationSlots.$inferSelect;

/** Webhook calls one tick makes: each is a fetch plus a write. */
const DISPATCH_BATCH_LIMIT = 5;
const DISPATCH_TIMEOUT_MS = 10_000;

export function serializeGenerator(g: GeneratorRow) {
	return {
		id: g.id,
		name: g.name,
		kind: g.kind,
		endpointUrl: g.endpointUrl,
		leadMinutes: g.leadMinutes,
		signed: Boolean(g.secretEnc),
		createdAt: g.createdAt
	};
}

export function serializeSlot(s: SlotRow, generatorName?: string) {
	return {
		id: s.id,
		generatorId: s.generatorId,
		generator: generatorName ?? null,
		projectId: s.projectId,
		title: s.title,
		brief: s.brief,
		sourceRef: slotSourceRef(s.id),
		plannedFor: s.plannedFor,
		requestAt: s.requestAt,
		status: s.status,
		attempts: s.attempts,
		lastError: s.lastError,
		requestedAt: s.requestedAt,
		draftId: s.draftId,
		createdAt: s.createdAt
	};
}

/** The secret is returned once, here, and stored only encrypted. */
export async function createGenerator(
	db: AppDb,
	env: Pick<AppEnv, 'APP_ENCRYPTION_KEY'>,
	userId: string,
	input: GeneratorInput
) {
	const existing = await first(
		db
			.select({ id: generators.id })
			.from(generators)
			.where(and(eq(generators.userId, userId), eq(generators.name, input.name)))
	);
	if (existing)
		throw Object.assign(new Error('A generator with that name exists'), { status: 409 });
	const secret = input.endpointUrl ? randomHex(32) : null;
	const now = new Date();
	const [row] = await db
		.insert(generators)
		.values({
			id: newId(),
			userId,
			name: input.name,
			kind: input.kind,
			endpointUrl: input.endpointUrl,
			secretEnc: secret ? await encryptSecret(secret, env.APP_ENCRYPTION_KEY) : null,
			leadMinutes: input.leadMinutes,
			createdAt: now,
			updatedAt: now
		})
		.returning();
	return { generator: serializeGenerator(row), secret };
}

export async function createSlot(db: AppDb, userId: string, input: SlotInput, now = new Date()) {
	const generator = await first(
		db
			.select()
			.from(generators)
			.where(and(eq(generators.id, input.generatorId), eq(generators.userId, userId)))
	);
	if (!generator) throw Object.assign(new Error('Generator not found'), { status: 404 });
	const lead = input.plannedFor.getTime() - generator.leadMinutes * 60_000;
	const [row] = await db
		.insert(generationSlots)
		.values({
			id: newId(),
			userId,
			generatorId: generator.id,
			projectId: input.projectId,
			title: input.title,
			brief: input.brief,
			plannedFor: input.plannedFor,
			// A release closer than the lead time is requested on the next tick.
			requestAt: new Date(Math.max(lead, now.getTime())),
			status: 'planned',
			createdAt: now,
			updatedAt: now
		})
		.returning();
	return serializeSlot(row, generator.name);
}

export async function listSlots(db: AppDb, userId: string) {
	const rows = await db
		.select({ slot: generationSlots, name: generators.name })
		.from(generationSlots)
		.innerJoin(generators, eq(generators.id, generationSlots.generatorId))
		.where(and(eq(generationSlots.userId, userId), ne(generationSlots.status, 'cancelled')))
		.orderBy(asc(generationSlots.plannedFor))
		.limit(500);
	return rows.map((r) => serializeSlot(r.slot, r.name));
}

/** What a generator receives, by webhook or by claiming. */
export function generationRequest(slot: SlotRow, generatorName: string, appUrl: string) {
	const base = appUrl.replace(/\/+$/, '');
	return {
		schema: GENERATION_REQUEST_SCHEMA,
		slotId: slot.id,
		generator: generatorName,
		projectId: slot.projectId,
		title: slot.title,
		brief: slot.brief,
		plannedFor: slot.plannedFor.toISOString(),
		intake: {
			sourceRef: slotSourceRef(slot.id),
			draftsUrl: `${base}/api/drafts`,
			mediaUrl: `${base}/api/drafts/{draftId}/media`
		}
	};
}

/**
 * Due slots for a generator that cannot be called: planned ones whose request
 * time has come become requested. Requested slots without media are returned
 * again, so an agent that crashed mid-render picks its work back up.
 */
export async function claimSlots(
	db: AppDb,
	userId: string,
	generatorName: string,
	appUrl: string,
	now = new Date()
) {
	const generator = await first(
		db
			.select()
			.from(generators)
			.where(and(eq(generators.userId, userId), eq(generators.name, generatorName)))
	);
	if (!generator) throw Object.assign(new Error('Generator not found'), { status: 404 });
	const due = await db
		.select()
		.from(generationSlots)
		.where(
			and(
				eq(generationSlots.generatorId, generator.id),
				or(
					and(eq(generationSlots.status, 'planned'), lte(generationSlots.requestAt, now)),
					eq(generationSlots.status, 'requested')
				)
			)
		)
		.orderBy(asc(generationSlots.plannedFor))
		.limit(20);
	const fresh = due.filter((slot) => slot.status === 'planned').map((slot) => slot.id);
	if (fresh.length) {
		await db
			.update(generationSlots)
			.set({ status: 'requested', requestedAt: now, updatedAt: now })
			.where(and(inArray(generationSlots.id, fresh), eq(generationSlots.status, 'planned')));
	}
	return due.map((slot) => generationRequest(slot, generator.name, appUrl));
}

export async function signRequest(secret: string, timestamp: string, body: string) {
	return `sha256=${await hmacHex(secret, `${timestamp}.${body}`)}`;
}

/**
 * Call cloud and API generators whose slots are due. A 2xx means the request
 * was accepted, not that media exists: the slot stays requested until the
 * generator pushes media to its sourceRef. Failures back off and park in
 * failed after MAX_DISPATCH_ATTEMPTS.
 */
export async function dispatchDueSlots(
	db: AppDb,
	env: Pick<AppEnv, 'APP_ENCRYPTION_KEY' | 'APP_URL'>,
	opts: { fetchImpl?: typeof fetch; budget?: SubrequestBudget | null; now?: Date } = {}
) {
	const now = opts.now ?? new Date();
	const doFetch = opts.fetchImpl ?? fetch;
	const due = await db
		.select({ slot: generationSlots, generator: generators })
		.from(generationSlots)
		.innerJoin(generators, eq(generators.id, generationSlots.generatorId))
		.where(
			and(
				eq(generationSlots.status, 'planned'),
				lte(generationSlots.requestAt, now),
				isNotNull(generators.endpointUrl)
			)
		)
		.orderBy(asc(generationSlots.requestAt))
		.limit(DISPATCH_BATCH_LIMIT);
	const results: Array<{ id: string; status: string }> = [];
	for (const { slot, generator } of due) {
		// A webhook and its write; leave room for what the tick still reports.
		if (opts.budget && opts.budget.remaining < 4) break;
		const body = JSON.stringify(generationRequest(slot, generator.name, env.APP_URL));
		let problem: string | null = null;
		try {
			if (!generator.secretEnc || !generator.endpointUrl)
				throw new Error('Generator has no endpoint');
			const secret = await decryptSecret(generator.secretEnc, env.APP_ENCRYPTION_KEY);
			const timestamp = String(now.getTime());
			opts.budget?.count();
			const response = await doFetch(generator.endpointUrl, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					'X-Fleet-Timestamp': timestamp,
					'X-Fleet-Signature': await signRequest(secret, timestamp, body)
				},
				body,
				signal: AbortSignal.timeout(DISPATCH_TIMEOUT_MS)
			});
			if (!response.ok) problem = `Generator answered HTTP ${response.status}`;
		} catch (err) {
			problem = err instanceof Error ? err.message : 'Generator request failed';
		}
		const attempts = slot.attempts + 1;
		const update = problem
			? {
					attempts,
					lastError: problem.slice(0, 500),
					status: attempts >= MAX_DISPATCH_ATTEMPTS ? 'failed' : 'planned',
					requestAt: new Date(now.getTime() + retryDelayMinutes(attempts) * 60_000),
					updatedAt: now
				}
			: { attempts, lastError: null, status: 'requested', requestedAt: now, updatedAt: now };
		await db
			.update(generationSlots)
			.set(update)
			.where(and(eq(generationSlots.id, slot.id), eq(generationSlots.status, 'planned')));
		results.push({ id: slot.id, status: update.status });
	}
	return results;
}

/**
 * A draft arriving through intake with a slot's sourceRef must belong to that
 * slot's project; it is linked so the slot can show progress.
 */
export async function slotForIntake(
	db: AppDb,
	userId: string,
	sourceRef: string | null,
	projectId: string
): Promise<{ ok: true; slot: SlotRow | null } | { ok: false; error: string }> {
	const slotId = slotIdFromSourceRef(sourceRef);
	if (!slotId) return { ok: true, slot: null };
	const slot = await first(
		db
			.select()
			.from(generationSlots)
			.where(and(eq(generationSlots.id, slotId), eq(generationSlots.userId, userId)))
	);
	if (!slot || slot.status === 'cancelled') return { ok: false, error: 'Unknown generation slot' };
	if (slot.projectId !== projectId) {
		return { ok: false, error: 'projectId does not match the generation slot' };
	}
	return { ok: true, slot };
}

export async function linkSlotDraft(db: AppDb, slotId: string, draftId: string) {
	await db
		.update(generationSlots)
		.set({ draftId, updatedAt: new Date() })
		.where(eq(generationSlots.id, slotId));
}

/** Media landed on a slot's draft: the generator has delivered. */
export async function markSlotReady(db: AppDb, sourceRef: string | null, draftId: string) {
	const slotId = slotIdFromSourceRef(sourceRef);
	if (!slotId) return;
	const now = new Date();
	await db
		.update(generationSlots)
		.set({ status: 'ready', draftId, lastError: null, updatedAt: now })
		.where(and(eq(generationSlots.id, slotId), ne(generationSlots.status, 'cancelled')));
}

export async function slotForDraft(db: AppDb, draftId: string) {
	return first(db.select().from(generationSlots).where(eq(generationSlots.draftId, draftId)));
}
