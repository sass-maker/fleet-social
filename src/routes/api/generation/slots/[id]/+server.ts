import { and, eq } from 'drizzle-orm';
import type { RequestHandler } from './$types';
import { first } from '$lib/server/db/client';
import { generationSlots } from '$lib/server/db/schema';
import { serializeSlot } from '$lib/server/generation';
import { fail, handleError, ok } from '$lib/server/http';
import { requireScope, requireUser } from '$lib/server/require';

/** `cancel` stops a slot that has not delivered; `retry` asks again now. */
export const PATCH: RequestHandler = async ({ params, request, locals }) => {
	try {
		const user = requireUser(locals.user);
		requireScope(locals, 'write');
		const body = (await request.json().catch(() => null)) as { action?: unknown } | null;
		const slot = await first(
			locals.db
				.select()
				.from(generationSlots)
				.where(and(eq(generationSlots.id, params.id), eq(generationSlots.userId, user.id)))
		);
		if (!slot) return fail('Not found', 404);
		const now = new Date();
		if (body?.action === 'cancel') {
			if (slot.status === 'ready') return fail('This slot already delivered its media', 409);
			const [row] = await locals.db
				.update(generationSlots)
				.set({ status: 'cancelled', updatedAt: now })
				.where(eq(generationSlots.id, slot.id))
				.returning();
			return ok({ slot: serializeSlot(row) });
		}
		if (body?.action === 'retry') {
			if (slot.status === 'ready' || slot.status === 'cancelled') {
				return fail(`A ${slot.status} slot cannot be retried`, 409);
			}
			const [row] = await locals.db
				.update(generationSlots)
				.set({ status: 'planned', attempts: 0, lastError: null, requestAt: now, updatedAt: now })
				.where(eq(generationSlots.id, slot.id))
				.returning();
			return ok({ slot: serializeSlot(row) });
		}
		return fail('action must be cancel or retry', 400);
	} catch (err) {
		return handleError(err);
	}
};
