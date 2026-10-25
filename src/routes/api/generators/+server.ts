import { asc, eq } from 'drizzle-orm';
import type { RequestHandler } from './$types';
import { parseGeneratorInput } from '$lib/domain/generation';
import { generators } from '$lib/server/db/schema';
import { createGenerator, serializeGenerator } from '$lib/server/generation';
import { fail, handleError, ok } from '$lib/server/http';
import { requireScope, requireSession, requireUser } from '$lib/server/require';

export const GET: RequestHandler = async ({ locals }) => {
	try {
		const user = requireUser(locals.user);
		requireScope(locals, 'read');
		const rows = await locals.db
			.select()
			.from(generators)
			.where(eq(generators.userId, user.id))
			.orderBy(asc(generators.name));
		return ok({ generators: rows.map(serializeGenerator) });
	} catch (err) {
		return handleError(err);
	}
};

// Session-only: registering a generator mints its signing secret.
export const POST: RequestHandler = async ({ request, locals }) => {
	try {
		const user = requireSession(locals.user, locals.authMethod);
		const parsed = parseGeneratorInput(await request.json().catch(() => null));
		if (!parsed.ok) return fail(parsed.error, 400);
		return ok(await createGenerator(locals.db, locals.env, user.id, parsed.value), 201);
	} catch (err) {
		return handleError(err);
	}
};
