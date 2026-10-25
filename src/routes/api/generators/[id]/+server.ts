import { and, eq } from 'drizzle-orm';
import type { RequestHandler } from './$types';
import { generators } from '$lib/server/db/schema';
import { fail, handleError, ok } from '$lib/server/http';
import { requireSession } from '$lib/server/require';

// Removing a generator removes its slots; drafts it already delivered stay.
export const DELETE: RequestHandler = async ({ params, locals }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		const removed = await locals.db
			.delete(generators)
			.where(and(eq(generators.id, params.id), eq(generators.userId, user.id)))
			.returning({ id: generators.id });
		if (!removed.length) return fail('Not found', 404);
		return ok({ ok: true });
	} catch (err) {
		return handleError(err);
	}
};
