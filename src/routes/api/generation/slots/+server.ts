import type { RequestHandler } from './$types';
import { isFleetProjectId } from '$lib/domain/fleet-projects';
import { parseSlotInput } from '$lib/domain/generation';
import { createSlot, listSlots } from '$lib/server/generation';
import { fail, handleError, ok } from '$lib/server/http';
import { requireScope, requireUser } from '$lib/server/require';

export const GET: RequestHandler = async ({ locals }) => {
	try {
		const user = requireUser(locals.user);
		requireScope(locals, 'read');
		return ok({ slots: await listSlots(locals.db, user.id) });
	} catch (err) {
		return handleError(err);
	}
};

export const POST: RequestHandler = async ({ request, locals }) => {
	try {
		const user = requireUser(locals.user);
		requireScope(locals, 'write');
		const parsed = parseSlotInput(await request.json().catch(() => null), isFleetProjectId);
		if (!parsed.ok) return fail(parsed.error, 400);
		return ok({ slot: await createSlot(locals.db, user.id, parsed.value) }, 201);
	} catch (err) {
		return handleError(err);
	}
};
