import { asc, eq } from 'drizzle-orm';
import type { PageServerLoad } from './$types';
import { generators } from '$lib/server/db/schema';
import { listSlots, serializeGenerator } from '$lib/server/generation';
import { requireSession } from '$lib/server/require';

export const load: PageServerLoad = async ({ locals }) => {
	const user = requireSession(locals.user, locals.authMethod);
	const [rows, slots] = await Promise.all([
		locals.db
			.select()
			.from(generators)
			.where(eq(generators.userId, user.id))
			.orderBy(asc(generators.name)),
		listSlots(locals.db, user.id)
	]);
	return { generators: rows.map(serializeGenerator), slots };
};
